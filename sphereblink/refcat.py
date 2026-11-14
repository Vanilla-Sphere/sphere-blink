"""Catalogue astrometry for the measurement pipeline.

The pipeline needs to know where an object should be on a given date: its
position at a reference epoch, proper motion and parallax. For stars that is
Gaia DR3. Gaia is asked at ESA first and at IRSA second, because either
service can be slow or down for a while.
"""

from __future__ import annotations

import csv
import io
import math

import httpx

from .measure import Body, offset
from .resolve import FEATURED, Target

ESA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
IRSA_TAP = "https://irsa.ipac.caltech.edu/TAP/sync"

# The challenge's test targets, with the best published astrometry.
# Gaia DR3 rows are copied from the ESA archive (epoch 2016.0).
TARGETS: dict[str, dict] = {
    "barnard": dict(kind="star", body=Body("Barnard's Star", 269.44850252544, 4.73942005111, 2016.0, -801.551, 10362.394, 546.976,
                                           8.19, "Gaia DR3", "4472832130942575872")),
    "eps-indi-b": dict(kind="brown_dwarf", body=Body("Epsilon Indi B", 331.07645259656, -56.79381206688, 2016.0, 3981.977, -2466.832,
                                                     270.658, 18.03, "Gaia DR3", "6412596012146801152")),
    # Not in Gaia (far too faint in visible light). Position: the AllWISE
    # designation J085510.83-071442.5, mean epoch 2010.5. Motion and parallax:
    # Kirkpatrick et al. 2021, ApJS 253, 7.
    "wise-0855": dict(kind="brown_dwarf", body=Body("WISE 0855-0714", 133.795129, -7.245147, 2010.5, -8123.7, 673.2, 439.0,
                                                    None, "AllWISE + Kirkpatrick 2021", "J085510.83-071442.5")),
    "rw-aur": dict(kind="variable", body=Body("RW Aurigae", 76.9565443110, 30.4013161499, 2016.0, 4.906, -24.578, 5.454,
                                              13.04, "Gaia DR3", "156430817820015232")),
    "r-dra": dict(kind="variable", body=Body("R Draconis", 248.1673783673, 66.7550067455, 2016.0, -20.643, 6.149, 1.251,
                                             6.37, "Gaia DR3", "1648328016354496384")),
}


def featured_key(name: str) -> str | None:
    """Key of a featured target by its display name."""
    for key, f in FEATURED.items():
        if f["name"].lower() == name.strip().lower():
            return key if key in TARGETS else None
    return None


def _num(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _rows(text: str) -> list[Body]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        ra, dec = _num(row.get("ra")), _num(row.get("dec"))
        if ra is None or dec is None:
            continue
        out.append(Body(
            name=f"Gaia DR3 {row.get('source_id', '').strip()}", ra=ra, dec=dec, epoch=2016.0,
            pmra=_num(row.get("pmra")) or 0.0, pmdec=_num(row.get("pmdec")) or 0.0,
            parallax=max(_num(row.get("parallax")) or 0.0, 0.0), mag=_num(row.get("phot_g_mean_mag")),
            catalog="Gaia DR3", catalog_id=str(row.get("source_id", "")).strip(),
        ))
    return out


def gaia_cone(ra: float, dec: float, radius_deg: float, gmax: float = 19.5, top: int = 400, timeout: float = 40.0) -> list[Body]:
    """Gaia DR3 sources around a position, brightest first."""
    cols = "source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag"
    where = f"1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra:.6f},{dec:.6f},{radius_deg:.5f})) AND phot_g_mean_mag < {gmax}"
    services = (
        (ESA_TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                   "QUERY": f"SELECT TOP {int(top)} {cols} FROM gaiadr3.gaia_source WHERE {where} ORDER BY phot_g_mean_mag"}),
        (IRSA_TAP, {"FORMAT": "CSV",
                    "QUERY": f"SELECT TOP {int(top)} {cols} FROM gaia_dr3_source WHERE {where} ORDER BY phot_g_mean_mag"}),
    )
    last: Exception | None = None
    for url, params in services:
        try:
            r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
            r.raise_for_status()
            if "source_id" not in r.text[:200]:
                raise ValueError("unexpected answer")
            return _rows(r.text)
        except Exception as exc:  # try the next service
            last = exc
    raise RuntimeError(f"Gaia is not reachable: {type(last).__name__}")


def body_for(target: Target, field: list[Body] | None = None, mjd: float = 61000.0) -> tuple[Body, str]:
    """The catalogue object to measure for what the user asked for, and its kind.

    A featured target comes from the table above. Anything else is matched
    to the Gaia star within 4 arcsec of the requested position (on the date
    of the SPHEREx data); with no match the position itself is measured, as
    an object without known motion.
    """
    key = featured_key(target.name)
    if key:
        return TARGETS[key]["body"], TARGETS[key]["kind"]
    best, best_r = None, 4.0
    for b in field or []:
        r = math.hypot(*offset(*b.at(mjd), target.ra, target.dec))
        if r < best_r:
            best, best_r = b, r
    if best is not None:
        named = target.source != "coordinates" and bool(target.name)
        return Body(target.name if named else best.name, best.ra, best.dec, best.epoch, best.pmra, best.pmdec, best.parallax,
                    best.mag, best.catalog, best.catalog_id), "star"
    name = target.name if target.source != "coordinates" and target.name else f"{target.ra:.5f} {target.dec:+.5f}"
    return Body(name, target.ra, target.dec, 2000.0 + (mjd - 51544.5) / 365.25, 0.0, 0.0, 0.0, None, "", ""), "candidate"
