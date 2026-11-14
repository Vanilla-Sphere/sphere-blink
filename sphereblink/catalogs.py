"""Overlay catalogues (Gaia DR3, CatWISE2020).

Gaia comes from the ESA Gaia archive, CatWISE from IRSA's cone search; IRSA's
TAP service, which holds both, is the fallback for each."""

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


ESA_TAP_URL = "https://gea.esac.esa.int/tap-server/tap/sync"
GATOR_URL = "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query"

# The same Gaia query against the archive that publishes the catalogue.
ESA_GAIA = (
    "SELECT TOP {top} source_id AS id, ra, dec, pmra, pmdec, pmra_error AS epmra, pmdec_error AS epmdec, "
    "phot_g_mean_mag AS mag, parallax "
    "FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra},{dec},{r})) "
    "ORDER BY phot_g_mean_mag"
)
GATOR_COLUMNS = "source_name,ra,dec,pmra,pmdec,sigpmra,sigpmdec,w2mpro,meanobsmjd,ab_flags"
GATOR_NAMES = {"source_name": "id", "sigpmra": "epmra", "sigpmdec": "epmdec", "w2mpro": "mag"}


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=min(8.0, seconds))


def _irsa_tap(cat: str, ra: float, dec: float, radius_deg: float, top: int, timeout: float) -> list[dict]:
    adql = QUERIES[cat].format(top=int(top), ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_deg:.5f}")
    r = httpx.get(TAP_URL, params={"QUERY": adql, "FORMAT": "CSV"}, timeout=_timeout(timeout), follow_redirects=True)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def _esa_gaia(cat: str, ra: float, dec: float, radius_deg: float, top: int, timeout: float) -> list[dict]:
    adql = ESA_GAIA.format(top=int(top), ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_deg:.5f}")
    r = httpx.get(ESA_TAP_URL, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql},
                  timeout=_timeout(timeout), follow_redirects=True)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if rows and "ra" not in rows[0]:
        raise ValueError("unexpected answer from the Gaia archive")
    return rows


def parse_ipac_table(text: str) -> list[dict]:
    """Rows of an IPAC fixed-width table (what Gator answers with); the
    column edges are the bars of the header line, "null" becomes empty."""
    lines = text.splitlines()
    header = next((ln for ln in lines if ln.startswith("|")), None)
    if header is None:
        raise ValueError("not an IPAC table")
    edges = [i for i, ch in enumerate(header) if ch == "|"]
    names = [header[a + 1 : b].strip() for a, b in zip(edges, edges[1:])]
    rows = []
    for ln in lines:
        if not ln.strip() or ln[0] in "\\|":
            continue
        cells = [ln[a + 1 : b + 1].strip() for a, b in zip(edges, edges[1:])]
        rows.append({n: ("" if c == "null" else c) for n, c in zip(names, cells)})
    return rows


def _gator_catwise(cat: str, ra: float, dec: float, radius_deg: float, top: int, timeout: float) -> list[dict]:
    """CatWISE through IRSA's cone-search service, which keeps answering when
    its TAP service is slow. It cannot sort, so a wide cone is asked for the
    bright end first and widened only if that holds fewer than ``top`` rows."""
    cuts = [None] if radius_deg <= 0.2 else [11.5, 14.0, None]
    rows: list[dict] = []
    for cut in cuts:
        params = {"catalog": "catwise_2020", "spatial": "cone", "objstr": f"{ra:.6f} {dec:+.6f}",
                  "radius": f"{radius_deg * 3600.0:.1f}", "radunits": "arcsec", "outfmt": "1", "selcols": GATOR_COLUMNS}
        if cut is not None:
            params["constraints"] = f"w2mpro<{cut}"
        r = httpx.get(GATOR_URL, params=params, timeout=_timeout(timeout), follow_redirects=True)
        r.raise_for_status()
        rows = parse_ipac_table(r.text)
        if len(rows) >= top:
            break
    rows = [{GATOR_NAMES.get(k, k): v for k, v in row.items()} for row in rows]
    rows.sort(key=lambda row: (_f(row.get("mag")) is None, _f(row.get("mag")) or 0.0))
    return rows[:top]


# Where each catalogue is asked for, in order: the quick service first, IRSA's
# TAP service (slow on a new field, at times unreachable) as the fallback.
SOURCES = {"gaia": (_esa_gaia, _irsa_tap), "catwise": (_gator_catwise, _irsa_tap)}


def query(cat: str, ra: float, dec: float, radius_deg: float, top: int = 250, timeout: float = 25.0) -> list[dict]:
    """Brightest sources around (ra, dec). Each has ra/dec at ``epoch`` (Julian
    year) and proper motion in mas/yr, so the viewer can move them to each
    frame's date. ``pm_snr`` is the proper motion divided by its error: faint
    CatWISE sources often list large but meaningless motions.

    Each catalogue has two services; the second is asked when the first fails
    or does not answer within ``timeout`` seconds."""
    if cat not in QUERIES:
        raise ValueError("cat must be gaia or catwise")
    rows, error = None, None
    for source in SOURCES[cat]:
        try:
            rows = source(cat, ra, dec, radius_deg, top, timeout)
            break
        except Exception as exc:  # the next service may still answer
            error = exc
    if rows is None:
        raise error
    out = []
    for row in rows:
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
