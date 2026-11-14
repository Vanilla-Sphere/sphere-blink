"""Turn user input (decimal / sexagesimal coordinates or an object name) into
an ICRS position, propagated by proper motion to the SPHEREx epoch."""

from __future__ import annotations

import html
import logging
import math
import re
from dataclasses import asdict, dataclass
from urllib.parse import quote

import httpx
from astropy import units as u
from astropy.coordinates import SkyCoord

log = logging.getLogger(__name__)

SPHEREX_EPOCH = 2025.9  # middle of the data available in late 2026

# Name services, tried in order. Sesame (CDS) asks SIMBAD, then NED, then
# VizieR and understands everyday names ("Vega", "M31"). CDS redirects to a
# mirror when Strasbourg is down; the Harvard mirrors are a second way in.
SESAME_URLS = (
    "https://cds.unistra.fr/cgi-bin/nph-sesame",
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame",
)
SIMBAD_TAP_URLS = (
    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
    "https://simbad.cfa.harvard.edu/simbad/sim-tap/sync",
)
NAME_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
MAX_NAME_LENGTH = 80


class ResolveError(ValueError):
    """The input could not be turned into a position."""


class NameNotFound(ResolveError):
    """The name services answered and do not know this name."""


class ResolverUnavailable(ResolveError):
    """No name service could be reached, so the name was not checked."""

# Challenge test targets. Positions are J2000 (epoch 2000.0) from SIMBAD,
# except WISE 0855 whose coordinates are the ones given in the challenge text.
FEATURED = {
    "barnard": dict(name="Barnard's Star", ra=269.452083, dec=4.693364, pmra=-802.80, pmdec=10362.54, epoch=2000.0,
                    note="Highest proper motion star (10.4 arcsec/yr)."),
    "eps-indi-b": dict(name="Epsilon Indi B", ra=331.043750, dec=-56.782694, pmra=3966.7, pmdec=-2536.2, epoch=2000.0,
                       note="Pair of brown dwarfs orbiting Epsilon Indi A."),
    "wise-0855": dict(name="WISE 0855-0714", ra=133.795129, dec=-7.245147, pmra=-8118.0, pmdec=673.0, epoch=2014.0,
                      note="Coldest known brown dwarf; ~8 arcsec/yr."),
    "rw-aur": dict(name="RW Aurigae", ra=76.956404, dec=30.401408, pmra=4.0, pmdec=-25.0, epoch=2000.0,
                   note="Young variable star (dimming events)."),
    "r-dra": dict(name="R Draconis", ra=248.167375, dec=66.754972, pmra=-5.0, pmdec=37.0, epoch=2000.0,
                  note="Mira variable, period ~246 days."),
}

ALIASES = {
    "barnard's star": "barnard", "barnards star": "barnard", "barnard star": "barnard", "gj 699": "barnard",
    "epsilon indi b": "eps-indi-b", "eps indi b": "eps-indi-b", "eps ind b": "eps-indi-b", "epsilon indi ba": "eps-indi-b",
    "wise 0855": "wise-0855", "wise 0855-0714": "wise-0855", "wise j085510.83-071442.5": "wise-0855",
    "rw aurigae": "rw-aur", "rw aur": "rw-aur", "r draconis": "r-dra", "r dra": "r-dra",
}


@dataclass
class Target:
    name: str
    ra: float  # position at SPHEREX_EPOCH
    dec: float
    ra_input: float
    dec_input: float
    pmra: float = 0.0  # mas/yr (includes cos dec)
    pmdec: float = 0.0
    epoch: float = 2000.0
    note: str = ""
    source: str = "coordinates"

    def to_dict(self) -> dict:
        return asdict(self)

    def position_at(self, year: float) -> tuple[float, float]:
        return propagate(self.ra_input, self.dec_input, self.pmra, self.pmdec, year - self.epoch)


def propagate(ra: float, dec: float, pmra: float, pmdec: float, dt_years: float) -> tuple[float, float]:
    dra = pmra / 3.6e6 * dt_years / max(math.cos(math.radians(dec)), 1e-6)
    ddec = pmdec / 3.6e6 * dt_years
    return (ra + dra) % 360.0, max(-90.0, min(90.0, dec + ddec))


_NUM = r"[-+]?\d+(?:\.\d*)?"


def parse_coords(text: str) -> tuple[float, float] | None:
    t = text.strip().replace(",", " ")
    m = re.fullmatch(rf"\s*({_NUM})\s+({_NUM})\s*", t)
    if m:
        ra, dec = float(m.group(1)), float(m.group(2))
        if 0 <= ra < 360 and -90 <= dec <= 90:
            return ra, dec
        return None
    if re.search(r"[hms:]|\d\s+\d", t):
        try:
            c = SkyCoord(t, unit=(u.hourangle, u.deg), frame="icrs")
            return float(c.ra.deg), float(c.dec.deg)
        except Exception:
            return None
    return None


def _from_featured(key: str) -> Target:
    f = FEATURED[key]
    ra, dec = propagate(f["ra"], f["dec"], f["pmra"], f["pmdec"], SPHEREX_EPOCH - f["epoch"])
    return Target(name=f["name"], ra=ra, dec=dec, ra_input=f["ra"], dec_input=f["dec"], pmra=f["pmra"],
                  pmdec=f["pmdec"], epoch=f["epoch"], note=f["note"], source="featured")


class _ServiceError(Exception):
    """One name service failed to give a usable answer."""


def _display_name(asked: str, main_id: str) -> str:
    """Catalogue name for display; keeps what the user typed when the catalogue calls it something else."""
    main = re.sub(r"^NAME\s+", "", " ".join(main_id.split()))

    def squash(x: str) -> str:
        return re.sub(r"\s+", "", x).lower()

    if not main or squash(main) == squash(asked):
        return main or asked
    return f"{asked} ({main})"


def _named(asked: str, main_id: str, ra: float, dec: float, pmra: float | None, pmdec: float | None,
           source: str) -> Target:
    pmra, pmdec = pmra or 0.0, pmdec or 0.0
    ra2, dec2 = propagate(ra, dec, pmra, pmdec, SPHEREX_EPOCH - 2000.0)
    return Target(name=_display_name(asked, main_id), ra=ra2, dec=dec2, ra_input=ra, dec_input=dec, pmra=pmra,
                  pmdec=pmdec, epoch=2000.0, source=source)


def _get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    try:
        r = client.get(url, timeout=NAME_TIMEOUT, follow_redirects=True, **kw)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _ServiceError(f"{exc.request.url.host}: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise _ServiceError(f"{httpx.URL(url).host}: {type(exc).__name__}") from exc
    return r


_SESAME_SOURCES = {"S": "SIMBAD", "N": "NED", "V": "VizieR"}


def _xml_value(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", block)
    return html.unescape(m.group(1)) if m else None


def parse_sesame(text: str, asked: str) -> Target | None:
    """Read a Sesame XML answer. None means Sesame answered and knows no such object."""
    if "<Sesame" not in text or "<Target" not in text:
        raise _ServiceError("Sesame: unexpected answer")
    for m in re.finditer(r'<Resolver\s+name="([^"]*)"\s*>(.*?)</Resolver>', text, re.S):
        block = m.group(2)
        try:
            ra, dec = float(_xml_value(block, "jradeg")), float(_xml_value(block, "jdedeg"))
        except (TypeError, ValueError):
            continue
        pm = [None, None]
        for i, tag in enumerate(("pmRA", "pmDE")):
            try:
                pm[i] = float(_xml_value(block, tag))
            except (TypeError, ValueError):
                pass
        source = _SESAME_SOURCES.get(m.group(1)[:1].upper(), "Sesame")
        return _named(asked, _xml_value(block, "oname") or asked, ra, dec, pm[0], pm[1], source)
    return None


def _sesame(name: str, client: httpx.Client) -> Target | None:
    """Ask Sesame. None means not known; _ServiceError means no host answered."""
    errors = []
    for base in SESAME_URLS:
        try:
            r = _get(client, f"{base}/-oxp/SNV?{quote(name, safe='')}")
            return parse_sesame(r.text, name)
        except _ServiceError as exc:
            errors.append(str(exc))
    raise _ServiceError("; ".join(errors))


def _simbad_ids(name: str) -> list[str]:
    """Spellings SIMBAD may store a name under: 'Vega' is 'NAME Vega', 'M31' is 'M 31' (or 'M  31')."""
    ids = [name, f"NAME {name}"]
    m = re.fullmatch(r"([A-Za-z*+]+)\s*(\d.*)", name)
    if m:
        ids += [f"{m.group(1)}{' ' * n}{m.group(2)}" for n in (1, 2, 3)]
    return list(dict.fromkeys(ids))


def _simbad(name: str, client: httpx.Client) -> Target | None:
    """Ask SIMBAD directly (TAP). None means not known; _ServiceError means no host answered."""
    ids = ", ".join("'{}'".format(i.replace("'", "''")) for i in _simbad_ids(name))
    q = (
        "SELECT TOP 1 b.main_id, b.ra, b.dec, b.pmra, b.pmdec FROM basic AS b "
        f"JOIN ident AS i ON i.oidref = b.oid WHERE i.id IN ({ids})"
    )
    errors = []
    for url in SIMBAD_TAP_URLS:
        try:
            r = _get(client, url, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": q})
            try:
                rows = r.json()["data"]
            except (ValueError, KeyError, TypeError) as exc:
                raise _ServiceError(f"{httpx.URL(url).host}: unexpected answer") from exc
        except _ServiceError as exc:
            errors.append(str(exc))
            continue
        if not rows or rows[0][1] is None or rows[0][2] is None:
            return None
        main_id, ra, dec, pmra, pmdec = rows[0]
        return _named(name, main_id, ra, dec, pmra, pmdec, "SIMBAD")
    raise _ServiceError("; ".join(errors))


def resolve_name(name: str, client: httpx.Client | None = None) -> Target:
    """Look an object name up: Sesame first, SIMBAD itself when Sesame is unreachable.

    Raises NameNotFound when a service answered and does not know the name, and
    ResolverUnavailable when none could be reached."""
    own = client is None
    client = client or httpx.Client()
    errors = []
    try:
        for service in (_sesame, _simbad):
            try:
                t = service(name, client)
            except _ServiceError as exc:
                log.warning("name lookup for %r failed: %s", name, exc)
                errors.append(str(exc))
                continue
            if t is None:
                raise NameNotFound(f"no object named '{name}' in SIMBAD, NED or VizieR; "
                                   "check the spelling or give coordinates")
            return t
    finally:
        if own:
            client.close()
    raise ResolverUnavailable(f"the name services (CDS Sesame, SIMBAD) are not answering, so '{name}' could not be "
                              "looked up; try again shortly or give coordinates")


def resolve(text: str, client: httpx.Client | None = None) -> Target:
    text = " ".join((text or "").split())
    if not text:
        raise ResolveError("empty target")
    key = text.lower()
    if key in FEATURED:
        return _from_featured(key)
    if key in ALIASES:
        return _from_featured(ALIASES[key])
    xy = parse_coords(text)
    if xy:
        return Target(name=f"RA {xy[0]:.5f} Dec {xy[1]:+.5f}", ra=xy[0], dec=xy[1], ra_input=xy[0], dec_input=xy[1],
                      epoch=SPHEREX_EPOCH)
    if len(text) > MAX_NAME_LENGTH:
        raise ResolveError(f"target name is longer than {MAX_NAME_LENGTH} characters")
    return resolve_name(text, client)
