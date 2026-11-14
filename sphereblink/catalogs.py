"""Overlay catalogues (Gaia DR3, CatWISE2020) queried through IRSA TAP."""

from __future__ import annotations

import csv
import io
import math

import httpx

TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"

QUERIES = {
    # Gaia DR3: positions at epoch 2016.0, proper motions in mas/yr.
    "gaia": (
        "SELECT TOP {top} source_id AS id, ra, dec, pmra, pmdec, pmra_error AS epmra, pmdec_error AS epmdec, "
        "phot_g_mean_mag AS mag, parallax "
        "FROM gaia_dr3_source WHERE CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{r}))=1 "
        "ORDER BY phot_g_mean_mag"
    ),
    # CatWISE2020: proper motions in arcsec/yr, epoch = meanobsmjd.
    "catwise": (
        "SELECT TOP {top} source_name AS id, ra, dec, pmra, pmdec, sigpmra AS epmra, sigpmdec AS epmdec, "
        "w2mpro AS mag, meanobsmjd "
        "FROM catwise_2020 WHERE CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{r}))=1 "
        "ORDER BY w2mpro"
    ),
}


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def query(cat: str, ra: float, dec: float, radius_deg: float, top: int = 250, timeout: float = 90.0) -> list[dict]:
    """Brightest sources around (ra, dec). Each has ra/dec at ``epoch`` (Julian
    year) and proper motion in mas/yr, so the viewer can move them to each
    frame's date. ``pm_snr`` is the proper motion divided by its error: faint
    CatWISE sources often list large but meaningless motions."""
    if cat not in QUERIES:
        raise ValueError("cat must be gaia or catwise")
    adql = QUERIES[cat].format(top=int(top), ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_deg:.5f}")
    r = httpx.get(TAP_URL, params={"QUERY": adql, "FORMAT": "CSV"}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        ra_s, dec_s = _f(row.get("ra")), _f(row.get("dec"))
        if ra_s is None or dec_s is None:
            continue
        pmra, pmdec = _f(row.get("pmra")) or 0.0, _f(row.get("pmdec")) or 0.0
        epmra, epmdec = _f(row.get("epmra")), _f(row.get("epmdec"))
        pm, snr = math.hypot(pmra, pmdec), 0.0
        if pm > 0 and epmra and epmdec:
            snr = pm * pm / math.hypot(pmra * epmra, pmdec * epmdec)
        if cat == "catwise":
            pmra, pmdec = pmra * 1000.0, pmdec * 1000.0
            mjd = _f(row.get("meanobsmjd")) or 57200.0
            epoch = 2000.0 + (mjd - 51544.5) / 365.25
        else:
            epoch = 2016.0
        out.append(
            {
                "id": str(row.get("id", "")).strip(),
                "ra": ra_s,
                "dec": dec_s,
                "pmra": round(pmra, 3),
                "pmdec": round(pmdec, 3),
                "pm_snr": round(snr, 1),
                "mag": _f(row.get("mag")),
                "parallax": _f(row.get("parallax")),
                "epoch": round(epoch, 3),
            }
        )
    return out
