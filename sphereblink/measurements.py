"""Measured objects (sources, detections, fitted tracks) from the team database.

Optional: without DATABASE_URL the viewer simply has no measurement overlay.
The tables are described in sql/README.md.
"""

from __future__ import annotations

import threading
import time

from . import config

_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
TTL_S = 60.0

_SOURCES = """
select s.id, s.name, s.kind, s.ra_deg, s.dec_deg, s.ref_epoch, s.pmra_masyr, s.pmdec_masyr,
       s.parallax_mas, s.gaia_source_id::text, s.catwise_id, s.note,
       t.epoch, t.ra0_deg, t.dec0_deg, t.pmra_masyr, t.pmdec_masyr, t.pmra_err, t.pmdec_err,
       t.n_detections, t.rms_arcsec
from sources_near(%s, %s, %s) s left join tracks t on t.source_id = s.id
order by s.name
"""
_DETECTIONS = """
select source_id, obs_id, detector, mjd, ra_deg, dec_deg, pos_err_arcsec, wave_um, flux_ujy, flux_err_ujy, flags, method
from detections where source_id = any(%s) order by mjd
"""


def enabled() -> bool:
    return bool(config.DB_URL)


def near(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """Sources within ``radius_deg`` with their fitted track and detections."""
    if not enabled():
        return []
    key = (round(ra, 5), round(dec, 5), round(radius_deg, 4))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL_S:
            return hit[1]
    import psycopg  # imported here so the service runs without the driver when no database is set

    out: dict[int, dict] = {}
    with psycopg.connect(config.DB_URL, connect_timeout=8, prepare_threshold=None) as conn:
        for r in conn.execute(_SOURCES, (ra, dec, radius_deg)).fetchall():
            out[r[0]] = {
                "name": r[1], "kind": r[2], "ra": r[3], "dec": r[4], "epoch": r[5],
                "pmra": r[6], "pmdec": r[7], "parallax": r[8], "gaia_source_id": r[9], "catwise_id": r[10], "note": r[11],
                "track": None if r[12] is None else {
                    "epoch": r[12], "ra": r[13], "dec": r[14], "pmra": r[15], "pmdec": r[16],
                    "pmra_err": r[17], "pmdec_err": r[18], "n_detections": r[19], "rms_arcsec": r[20],
                },
                "detections": [],
            }
        if out:
            for r in conn.execute(_DETECTIONS, (list(out),)).fetchall():
                out[r[0]]["detections"].append({
                    "obs_id": r[1], "detector": r[2], "mjd": r[3], "ra": r[4], "dec": r[5], "pos_err_arcsec": r[6],
                    "wave_um": r[7], "flux_ujy": r[8], "flux_err_ujy": r[9], "flags": r[10], "method": r[11],
                })
    result = list(out.values())
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (time.time(), result)
    return result
