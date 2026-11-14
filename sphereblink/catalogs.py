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
        "w2mpro AS mag, meanobsmjd, ab_flags "
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


# CatWISE splits the halo, spikes and ghosts of a bright star into many
# catalogue entries with large, confident and wrong proper motions (around
# Barnard's star 44 of the 250 brightest entries "move" faster than 0.15
# arcsec/yr). Measured on Barnard, eps Indi A, RW Aur and the WISE 0855 field
# (2026-11-14): the fakes reach 210 arcsec from a W2 = 4.5 star and about 90
# from a W2 = 7 star, and stars too bright to be in the catalogue at all
# (eps Indi A) still leave a crowd of them. Real fast movers are rare and
# alone, apart from the few pieces one object is split into along its path.
FAST_MAS_YR = 150.0
FAST_SNR = 5.0
BRIGHT_W2 = 8.5
CROWD_ARCSEC = 200.0
CROWD = 4
SAME_OBJECT_ARCSEC = 30.0  # pieces of one moving object lie this close and move alike
PM_LIMIT_MAS_YR = 15000.0  # faster than Barnard's star: not a real motion


def _halo_arcsec(w2: float) -> float:
    return min(600.0, 210.0 * 10 ** (-0.156 * (w2 - 4.5)))


def _fast(s: dict) -> bool:
    return math.hypot(s["pmra"], s["pmdec"]) > FAST_MAS_YR and s["pm_snr"] >= FAST_SNR


def flag_artifacts(sources: list[dict]) -> list[dict]:
    """Mark CatWISE entries whose proper motion cannot be trusted with
    ``suspect: True`` and set that motion to zero, so the viewer draws them
    where the catalogue saw them and does not call them fast movers.

    A "fast" entry is suspect when it is a saturated star, lies in the halo
    of a bright entry, carries a CatWISE artifact flag, reports an impossible
    motion, or has a crowd of other "fast" entries around it (the mark of a
    bright star nearby, even one outside the list)."""

    def sep(a, b):
        return 3600.0 * math.hypot((a["ra"] - b["ra"]) * math.cos(math.radians(a["dec"])), a["dec"] - b["dec"])

    def same_object(a, b):
        big = max(math.hypot(a["pmra"], a["pmdec"]), math.hypot(b["pmra"], b["pmdec"]))
        return sep(a, b) < SAME_OBJECT_ARCSEC and math.hypot(a["pmra"] - b["pmra"], a["pmdec"] - b["pmdec"]) < 0.4 * big

    bright = [s for s in sources if s["mag"] is not None and s["mag"] < BRIGHT_W2]
    fast = [s for s in sources if _fast(s)]
    suspect = []
    for s in fast:
        crowd = []  # other fast entries around this one, pieces of one object counted once
        for o in fast:
            if o is not s and sep(s, o) < CROWD_ARCSEC and not same_object(s, o) and not any(same_object(o, c) for c in crowd):
                crowd.append(o)
        halo = any(o is not s and sep(s, o) < _halo_arcsec(o["mag"]) for o in bright)
        flagged = any(c not in "0 " for c in s.get("ab_flags") or "")  # CatWISE: halo, spike, ghost or latent
        if len(crowd) >= CROWD or halo or flagged or s in bright or math.hypot(s["pmra"], s["pmdec"]) > PM_LIMIT_MAS_YR:
            suspect.append(s)
    for s in suspect:
        s.update(pmra=0.0, pmdec=0.0, pm_snr=0.0, suspect=True)
    for s in sources:
        s.pop("ab_flags", None)
    return sources


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
                **({"ab_flags": (row.get("ab_flags") or "").strip()} if cat == "catwise" else {}),
            }
        )
    return flag_artifacts(out) if cat == "catwise" else out
