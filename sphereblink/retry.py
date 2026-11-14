"""Ask an outside archive again when its failure is a passing one.

IRSA sits behind a gateway that at times answers "502 Proxy Error" within
half a second while the same request, sent again, is served normally
(seen on 2026-11-15: two requests in three for several minutes, image
search and TAP alike). A fixed number of tries is the wrong tool for that:
two quick tries fail together, twenty slow ones hold a view up for minutes.
Here a request is repeated, with a growing and uneven pause, until a
deadline; what the caller gets in the end is the answer or one exception
with a sentence fit for a reader.
"""

from __future__ import annotations

import logging
import random
import time

import httpx

log = logging.getLogger("uvicorn.error")  # the server's own log, which the host keeps

TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
FIRST_PAUSE_S = 0.5
LONGEST_PAUSE_S = 4.0
SHORTEST_TRY_S = 5.0  # a try that starts just before the deadline still gets this long


class Unavailable(Exception):
    """An outside service gave no usable answer in the time allowed."""

    def __init__(self, service: str, reason: str, tries: int = 1):
        self.service, self.reason, self.tries = service, reason, tries
        again = f", asked {tries} times" if tries > 1 else ""
        super().__init__(f"{service} is not answering right now ({reason}{again}). This usually passes within minutes.")


def reason_of(exc: Exception) -> str:
    """Why a request failed, in a few words and without the address asked."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "no answer in time"
    if isinstance(exc, httpx.TransportError):
        return "connection failed"
    return type(exc).__name__


def transient(exc: Exception) -> bool:
    """Could the same request succeed a moment later?"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS
    return isinstance(exc, httpx.TransportError)  # timeouts, refused or dropped connections


def _asked_to_wait(exc: Exception) -> float:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return max(0.0, float(exc.response.headers.get("retry-after", "")))
        except ValueError:
            pass
    return 0.0


def get(
    url: str,
    params: dict | None = None,
    *,
    service: str,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    budget: float = 30.0,
    deadline: float | None = None,
    sleep=time.sleep,
    clock=time.monotonic,
    **kw,
) -> httpx.Response:
    """GET ``url``; a passing failure (gateway error, timeout, dropped
    connection) is tried again until ``budget`` seconds have gone by, or
    until ``deadline`` (a ``clock`` time shared by several requests).

    Raises ``Unavailable`` when no good answer came; a refusal that asking
    again cannot change (HTTP 400, 404, ...) raises it at once."""
    deadline = clock() + budget if deadline is None else deadline
    ask = client.get if client is not None else httpx.get
    tries, why = 0, ""
    while True:
        tries += 1
        left = deadline - clock()
        try:
            r = ask(url, params=params, timeout=httpx.Timeout(min(timeout, max(left, SHORTEST_TRY_S)), connect=min(8.0, timeout)),
                    follow_redirects=True, **kw)
            r.raise_for_status()
            if tries > 1:  # how often the archives stumble is read from these lines
                log.info("%s answered at try %d (before that: %s)", service, tries, why)
            return r
        except httpx.HTTPError as exc:
            why = reason_of(exc)
            if not transient(exc):
                raise Unavailable(service, why, tries) from exc
            # 0.5, 1, 2, 4, 4 ... seconds, each shortened by up to half so that
            # many callers do not come back at the same instant
            pause = min(LONGEST_PAUSE_S, FIRST_PAUSE_S * 2 ** (tries - 1)) * random.uniform(0.5, 1.0)
            pause = max(pause, min(_asked_to_wait(exc), 15.0))
            if clock() + pause >= deadline:
                log.warning("%s gave no answer in %d tries (%s)", service, tries, why)
                raise Unavailable(service, why, tries) from exc
            sleep(pause)
