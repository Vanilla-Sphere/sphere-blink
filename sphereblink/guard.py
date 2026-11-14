"""Protection for a public API that nobody logs in to.

Three things live here, all as one ASGI middleware (``Guard``):

* who is calling (``client_of``): the address Cloudflare saw, so that one
  visitor cannot pose as many by sending forwarding headers of their own;
* how much one caller may ask for: a request rate, a number of requests
  open at once, and token buckets for the expensive actions (looking a name
  up at the name services, starting a measurement, making the server fetch
  tiles of the sky photograph). The numbers are far
  above what a person or a classroom behind one address needs and are there
  to stop a script;
* the response headers browsers act on (content security policy, no
  sniffing, no framing by other sites, HTTPS only).

A refused request gets ``429`` (this caller asks too much) or ``503`` (the
server as a whole is full) with a ``Retry-After`` header and a JSON
``detail`` the viewer shows.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import threading
import time
from contextvars import ContextVar

from . import config, settings

log = logging.getLogger("uvicorn.error")  # the server's own log, which the host keeps
_client: ContextVar[str] = ContextVar("sphereblink_client", default="")
_seen_headers = False


def current() -> str:
    """The caller of the request being answered ("" outside a request)."""
    return _client.get()


class Busy(Exception):
    """This request is refused for now; ``retry_after`` seconds later it may pass."""

    def __init__(self, detail: str, retry_after: float = 5.0, status: int = 429):
        super().__init__(detail)
        self.detail, self.retry_after, self.status = detail, max(1.0, retry_after), status


# ----------------------------------------------------------------------------- who is calling
def _normal(ip: str) -> str:
    """One key per visitor: an IPv4 address, or the /64 an IPv6 address is in
    (a home or phone gets a whole /64, so single addresses would be free to change)."""
    try:
        a = ipaddress.ip_address(ip.strip().strip("[]"))
    except ValueError:
        return "unknown"
    if a.version == 6:
        if a.ipv4_mapped:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False).network_address) + "/64"
    return str(a)


def client_of(scope) -> str:
    """Address of the caller. The service sits behind Cloudflare and Render's
    proxy: ``CF-Connecting-IP`` is written by Cloudflare itself and cannot be
    set by the visitor, unlike the first entry of ``X-Forwarded-For``."""
    headers = {k: v for k, v in scope.get("headers") or ()}
    for name in (b"cf-connecting-ip", b"true-client-ip"):
        v = headers.get(name)
        if v:
            return _normal(v.decode("latin-1"))
    xff = headers.get(b"x-forwarded-for")
    if xff:  # no Cloudflare in the path: the first entry is the visitor, as Render documents
        return _normal(xff.decode("latin-1").split(",")[0])
    peer = scope.get("client")
    return _normal(peer[0]) if peer else "unknown"


_INSIDE = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                             "::1/128", "fc00::/7", "fe80::/10")]


def internal(client: str) -> bool:
    """Calls from inside (the host's health checks, the server itself) are never limited."""
    try:
        a = ipaddress.ip_address(client)
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in _INSIDE)


# ----------------------------------------------------------------------------- how much
class Buckets:
    """Token buckets, one per caller: ``burst`` at once, then ``per_second``."""

    def __init__(self, per_second: float, burst: float, max_clients: int = 20000):
        self.rate, self.burst, self.max = float(per_second), float(burst), max_clients
        self._b: dict[str, tuple[float, float]] = {}  # client -> (tokens, when)
        self._lock = threading.Lock()

    def take(self, client: str, cost: float = 1.0) -> float:
        """Take ``cost`` tokens. Returns 0 when allowed, else the seconds to wait."""
        if self.rate <= 0 or not client or internal(client):
            return 0.0
        now = time.monotonic()
        with self._lock:
            tokens, when = self._b.get(client, (self.burst, now))
            tokens = min(self.burst, tokens + (now - when) * self.rate)
            if tokens < cost:
                self._b[client] = (tokens, now)
                return (cost - tokens) / self.rate
            self._b[client] = (tokens - cost, now)
            if len(self._b) > self.max:  # forget the callers whose bucket has refilled
                full = [c for c, (t, w) in self._b.items() if t + (now - w) * self.rate >= self.burst]
                for c in full:
                    self._b.pop(c, None)
                if len(self._b) > self.max:
                    self._b.clear()
            return 0.0

    def left(self, client: str) -> int:
        now = time.monotonic()
        with self._lock:
            tokens, when = self._b.get(client, (self.burst, now))
            return int(min(self.burst, tokens + (now - when) * self.rate))


requests = Buckets(config.RATE_REQUESTS_PER_S, config.RATE_REQUESTS_BURST)
names = Buckets(config.RATE_NAMES_PER_MIN / 60.0, config.RATE_NAMES_PER_MIN)
measures = Buckets(config.RATE_MEASURES_PER_HOUR / 3600.0, config.RATE_MEASURES_BURST)
catalogs = Buckets(config.RATE_CATALOGS_PER_MIN / 60.0, config.RATE_CATALOGS_PER_MIN)
sky_tiles = Buckets(config.RATE_TILES_PER_S, config.RATE_TILES_BURST)  # tiles of the sky photograph asked for
atlas = Buckets(config.RATE_ATLAS_PER_MIN / 60.0, config.RATE_ATLAS_PER_MIN)  # ... and those the server must fetch from the archive
TILES = "/api/atlas/2mass/"


def _rates_changed() -> None:
    """The allowances are settings that can change while the server runs
    (``settings.py``): a caller keeps what is left in the bucket, under the
    new numbers."""
    g = settings.get
    for bucket, per_second, burst in (
        (requests, g("rate_requests_per_s"), g("rate_requests_burst")),
        (names, g("rate_names_per_min") / 60.0, g("rate_names_per_min")),
        (measures, g("rate_measures_per_hour") / 3600.0, g("rate_measures_burst")),
        (catalogs, g("rate_catalogs_per_min") / 60.0, g("rate_catalogs_per_min")),
        (sky_tiles, g("rate_tiles_per_s"), g("rate_tiles_burst")),
        (atlas, g("rate_atlas_per_min") / 60.0, g("rate_atlas_per_min")),
    ):
        bucket.rate, bucket.burst = float(per_second), float(burst)


settings.on_change(_rates_changed)


def charge(bucket: Buckets, what: str, cost: float = 1.0) -> None:
    """Charge the caller of this request for an expensive action; raises Busy when the bucket is empty."""
    wait = bucket.take(current(), cost)
    if wait > 0:
        raise Busy(f"too many {what} from your address; try again in {math.ceil(wait)} s", wait)


_open: dict[str, int] = {}  # requests being answered, per caller
_open_lock = threading.Lock()


# ----------------------------------------------------------------------------- response headers
# The viewer loads everything from its own origin, with one exception: the
# photograph behind the zoomed-in sky map is made of tiles of the 2MASS atlas.
# They come from this server (/api/atlas/2mass/, which keeps them on disk), and
# a tile this server cannot give is asked of the NASA/IPAC Infrared Science
# Archive itself, as an image. Inline style attributes are used for colour
# swatches; there is no inline script.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https://irsa.ipac.caltech.edu; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
)
# The interactive API pages (/docs, /redoc) are FastAPI's: they load Swagger UI
# or ReDoc from jsDelivr and start it from an inline script.
CSP_DOCS = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; worker-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'self'"
)
ALWAYS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"strict-transport-security", b"max-age=31536000"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
]


# No other site may show a page of this one inside a frame of its own (a
# hidden frame under a decoy is how a visitor's click is stolen). Two headers
# say it: ``frame-ancestors`` in the policies above, which every current
# browser obeys, and this one, which older ones know. Only pages carry them:
# a picture or data may be shown anywhere.
FRAMING = (b"x-frame-options", b"SAMEORIGIN")


def security_headers(path: str, content_type: bytes, status: int = 200) -> list[tuple[bytes, bytes]]:
    out = list(ALWAYS)
    if status == 304:
        # "Not modified" has no body and no type, so what it answers for is
        # unknown here, and a browser lays its headers over the ones it kept:
        # sent with the policy of data, it would take the page's policy (and
        # with it frame-ancestors) away. The kept answer's headers stand.
        return out
    if content_type.startswith((b"text/html", b"application/xhtml+xml")):
        docs = path in ("/docs", "/redoc") or path.startswith("/docs/")
        out += [
            (b"content-security-policy", (CSP_DOCS if docs else CSP).encode()),
            FRAMING,
            (b"cross-origin-opener-policy", b"same-origin"),
        ]
    else:
        # data and pictures: never a document, and other sites may show them (<img>, fetch)
        out += [(b"content-security-policy", b"default-src 'none'"), (b"cross-origin-resource-policy", b"cross-origin")]
    return out


# ----------------------------------------------------------------------------- the middleware
class Guard:
    """Names the caller, applies the request limits and adds the headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        client = client_of(scope)
        token = _client.set(client)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                headers += [(k, v) for k, v in security_headers(path, ctype, message.get("status", 200)) if k not in have]
                message = {**message, "headers": headers}
            await send(message)

        limited = path.startswith("/api/") and path != "/api/health" and not internal(client)
        counted = False
        global _seen_headers
        if limited and not _seen_headers:  # once: which proxy headers reach us (names only), to check client_of
            _seen_headers = True
            log.info("guard: first outside request carries %s", sorted(
                k.decode("latin-1") for k, _ in scope.get("headers") or () if k.startswith((b"cf-", b"true-", b"x-forwarded", b"forwarded", b"x-real"))))
        try:
            if limited and path.startswith(TILES):
                # Tiles of the sky photograph have an allowance of their own
                # and are not counted as open requests: a classroom behind one
                # address looking at the map must not use up what its views
                # and measurements need. A refused tile is asked of the
                # archive by the page itself.
                wait = sky_tiles.take(client)
                if wait > 0:
                    return await _refuse(send_with_headers, Busy("too many sky tiles from your address; slow down", wait))
            elif limited:
                wait = requests.take(client)
                if wait > 0:
                    return await _refuse(send_with_headers, Busy("too many requests from your address; slow down", wait))
                with _open_lock:
                    n = _open.get(client, 0)
                    if n >= settings.get("max_open_requests"):
                        busy = Busy("too many requests open at once from your address", 2.0)
                    else:
                        _open[client], counted, busy = n + 1, True, None
                if busy:
                    return await _refuse(send_with_headers, busy)
            await self.app(scope, receive, send_with_headers)
        finally:
            if counted:
                with _open_lock:
                    n = _open.get(client, 1) - 1
                    if n <= 0:
                        _open.pop(client, None)
                    else:
                        _open[client] = n
            _client.reset(token)


async def _refuse(send, busy: Busy) -> None:
    log.warning("guard: refused %s (%s)", current(), busy.detail)
    body = json.dumps({"detail": busy.detail}).encode()
    await send({"type": "http.response.start", "status": busy.status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"retry-after", str(math.ceil(busy.retry_after)).encode()), (b"cache-control", b"no-store"),
        (b"access-control-allow-origin", b"*"),
    ]})
    await send({"type": "http.response.body", "body": body})
