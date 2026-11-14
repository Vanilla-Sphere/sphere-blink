"""Turn user input (decimal / sexagesimal coordinates or an object name) into
an ICRS position, propagated by proper motion to the SPHEREx epoch."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

import httpx
from astropy import units as u
from astropy.coordinates import SkyCoord

SPHEREX_EPOCH = 2025.9  # middle of the data available in late 2026

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


def _simbad(name: str, client: httpx.Client) -> Target | None:
    q = (
        "SELECT TOP 1 b.main_id, b.ra, b.dec, b.pmra, b.pmdec FROM basic AS b "
        "JOIN ident AS i ON i.oidref = b.oid WHERE i.id = '{}'"
    ).format(name.replace("'", "''"))
    r = client.get(
        "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
        params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": q},
    )
    r.raise_for_status()
    rows = r.json().get("data") or []
    if not rows:
        return None
    main_id, ra, dec, pmra, pmdec = rows[0]
    if ra is None or dec is None:
        return None
    pmra, pmdec = pmra or 0.0, pmdec or 0.0
    ra2, dec2 = propagate(ra, dec, pmra, pmdec, SPHEREX_EPOCH - 2000.0)
    return Target(name=main_id, ra=ra2, dec=dec2, ra_input=ra, dec_input=dec, pmra=pmra, pmdec=pmdec,
                  epoch=2000.0, source="SIMBAD")


def resolve(text: str, client: httpx.Client | None = None) -> Target:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty target")
    key = text.lower()
    if key in FEATURED:
        return _from_featured(key)
    if key in ALIASES:
        return _from_featured(ALIASES[key])
    xy = parse_coords(text)
    if xy:
        return Target(name=f"RA {xy[0]:.5f} Dec {xy[1]:+.5f}", ra=xy[0], dec=xy[1], ra_input=xy[0], dec_input=xy[1],
                      epoch=SPHEREX_EPOCH)
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        t = _simbad(text, client)
    except httpx.HTTPError:
        t = None
    finally:
        if own:
            client.close()
    if t is None:
        raise ValueError(f"could not resolve '{text}'")
    return t
