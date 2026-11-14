"""Measured objects: sources, their position in each exposure, fitted tracks.

Two places hold them:

* a small SQLite file next to the cutout cache, written by the measurement
  pipeline (``measure_api.py``). It always exists, so the viewer shows
  measurements even when no database is configured;
* the team's Postgres database (``DATABASE_URL``, tables in sql/README.md),
  where the pipeline also writes when its role is allowed to, and where
  people can load measurements of their own.

``near`` reads both.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time

from . import config

log = logging.getLogger(__name__)

_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
TTL_S = 60.0
PIPELINE_METHODS = ["psf", "wings", "stack"]  # detections.method values the pipeline owns

_SOURCES = """
select s.id, s.name, s.kind, s.ra_deg, s.dec_deg, s.ref_epoch, s.pmra_masyr, s.pmdec_masyr,
       s.parallax_mas, s.gaia_source_id::text, s.catwise_id, s.note,
       t.epoch, t.ra0_deg, t.dec0_deg, t.pmra_masyr, t.pmdec_masyr, t.pmra_err, t.pmdec_err,
       t.n_detections, t.rms_arcsec, t.model, t.summary
from sources_near(%s, %s, %s) s left join tracks t on t.source_id = s.id
order by s.name
"""
_DETECTIONS = """
select source_id, obs_id, detector, mjd, ra_deg, dec_deg, pos_err_arcsec, wave_um, flux_ujy, flux_err_ujy, flags, method,
       east_arcsec, north_arcsec, snr, n_exposures, used, rel_flux
from detections where source_id = any(%s) order by mjd
"""


def enabled() -> bool:
    """Is there anything to show: a database, or measurements of our own?"""
    return bool(config.DB_URL) or has_local()


def db_enabled() -> bool:
    return bool(config.DB_URL)


# ----------------------------------------------------------------------------- local store
_local = threading.local()


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    path = str(config.CACHE_DIR / "measurements.sqlite")
    if c is None or getattr(_local, "path", "") != path:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=30, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, name TEXT NOT NULL, ra REAL NOT NULL, "
                  "dec REAL NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)")
        _local.c, _local.path = c, path
    return c


def save_local(key: str, result: dict) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)",
                  (key, result["name"], result["ra"], result["dec"], time.time(), json.dumps(result)))
    with _lock:
        _cache.clear()


def delete_local(key: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM results WHERE key=?", (key,))
    with _lock:
        _cache.clear()


def load_local(key: str) -> dict | None:
    row = _conn().execute("SELECT payload FROM results WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def has_local() -> bool:
    try:
        return _conn().execute("SELECT 1 FROM results LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def list_local() -> list[dict]:
    """Every result of the pipeline, without the detections."""
    out = []
    for key, payload in _conn().execute("SELECT key, payload FROM results ORDER BY name").fetchall():
        r = json.loads(payload)
        r.pop("detections", None)
        out.append({"key": key, **r})
    return out


def _sep_deg(ra1, dec1, ra2, dec2) -> float:
    a = math.sin(math.radians(dec2 - dec1) / 2) ** 2 + math.cos(math.radians(dec1)) * math.cos(math.radians(dec2)) * math.sin(
        math.radians(ra2 - ra1) / 2) ** 2
    return 2 * math.degrees(math.asin(min(1.0, math.sqrt(a))))


def to_source(result: dict) -> dict:
    """A pipeline result in the shape ``/api/tracks`` serves."""
    b, tr = result["target"], result.get("track")
    gaia = b.get("catalog_id") if b.get("catalog") == "Gaia DR3" else None
    return {
        "name": result["name"], "kind": result.get("kind", "candidate"), "ra": b["ra"], "dec": b["dec"], "epoch": b["epoch"],
        "pmra": b["pmra"], "pmdec": b["pmdec"], "parallax": b.get("parallax") or None, "gaia_source_id": gaia,
        "catwise_id": None, "note": result.get("note", ""),
        "track": None if not tr else {
            "epoch": tr["epoch"], "ra": tr["ra"], "dec": tr["dec"], "pmra": tr["pmra"], "pmdec": tr["pmdec"],
            "pmra_err": tr["pmra_err"], "pmdec_err": tr["pmdec_err"], "n_detections": tr["n_detections"],
            "rms_arcsec": tr["rms_arcsec"], "model": tr["model"], "summary": _summary(result),
        },
        "detections": [{
            "obs_id": d["obs_id"], "detector": d["detector"], "mjd": d["mjd"], "ra": d["ra"], "dec": d["dec"],
            "pos_err_arcsec": d["pos_err_arcsec"], "wave_um": d["wave_um"], "flux_ujy": d["flux_ujy"],
            "flux_err_ujy": d["flux_err_ujy"], "flags": d["flags"], "method": d["method"], "east": d["east"],
            "north": d["north"], "snr": d["snr"], "n_exposures": d["n_exposures"], "used": d["used"], "rel_flux": d["rel_flux"],
        } for d in result.get("detections", [])],
        "measured_by": "pipeline",
    }


def _summary(result: dict) -> dict:
    """What is kept with the track besides the motion itself."""
    return {"track": result.get("track"), "light_curve": result.get("light_curve"), "stats": result.get("stats"),
            "catalog": {k: result["target"].get(k) for k in ("catalog", "catalog_id", "epoch", "pmra", "pmdec", "parallax")},
            "measured_at": result.get("measured_at"), "version": result.get("version")}


def local_near(ra: float, dec: float, radius_deg: float) -> list[dict]:
    out = []
    try:
        rows = _conn().execute("SELECT ra, dec, payload FROM results WHERE dec BETWEEN ? AND ?",
                               (dec - radius_deg, dec + radius_deg)).fetchall()
    except sqlite3.Error:
        return out
    for r_ra, r_dec, payload in rows:
        if _sep_deg(ra, dec, r_ra, r_dec) <= radius_deg:
            result = json.loads(payload)
            if result.get("track"):  # a search that found nothing is not a measured source
                out.append(to_source(result))
    return out


# ----------------------------------------------------------------------------- team database
def _connect():
    import psycopg  # imported here so the service runs without the driver when no database is set

    return psycopg.connect(config.DB_URL, connect_timeout=8, prepare_threshold=None)


def db_near(ra: float, dec: float, radius_deg: float) -> list[dict]:
    out: dict[int, dict] = {}
    with _connect() as conn:
        for r in conn.execute(_SOURCES, (ra, dec, radius_deg)).fetchall():
            out[r[0]] = {
                "name": r[1], "kind": r[2], "ra": r[3], "dec": r[4], "epoch": r[5],
                "pmra": r[6], "pmdec": r[7], "parallax": r[8], "gaia_source_id": r[9], "catwise_id": r[10], "note": r[11],
                "track": None if r[12] is None else {
                    "epoch": r[12], "ra": r[13], "dec": r[14], "pmra": r[15], "pmdec": r[16],
                    "pmra_err": r[17], "pmdec_err": r[18], "n_detections": r[19], "rms_arcsec": r[20],
                    "model": r[21], "summary": r[22],
                },
                "detections": [],
            }
        if out:
            for r in conn.execute(_DETECTIONS, (list(out),)).fetchall():
                out[r[0]]["detections"].append({
                    "obs_id": r[1], "detector": r[2], "mjd": r[3], "ra": r[4], "dec": r[5], "pos_err_arcsec": r[6],
                    "wave_um": r[7], "flux_ujy": r[8], "flux_err_ujy": r[9], "flags": r[10], "method": r[11],
                    "east": r[12], "north": r[13], "snr": r[14], "n_exposures": r[15], "used": r[16], "rel_flux": r[17],
                })
    return list(out.values())


def save_db(result: dict) -> bool:
    """Write a pipeline result to the team database (sources, detections,
    tracks). Returns False when there is no database or its role may not
    write; the result is in the local store either way. A result without a
    track (nothing was detected) is not written: it only takes the
    pipeline's earlier rows for that source away."""
    if not db_enabled():
        return False
    src = to_source(result)
    tr = src["track"]
    if not tr:
        try:
            with _connect() as conn:
                row = conn.execute("select id from sources where name = %s", (src["name"],)).fetchone()
                if row:
                    conn.execute("delete from detections where source_id = %s and method = any(%s)", (row[0], PIPELINE_METHODS))
                    conn.execute("delete from tracks where source_id = %s", (row[0],))
            with _lock:
                _cache.clear()
        except Exception as exc:
            log.warning("could not clear %s in the measurement database: %s: %s", result.get("name"), type(exc).__name__, exc)
        return False
    try:
        with _connect() as conn:
            sid = conn.execute(
                "insert into sources (name, kind, ra_deg, dec_deg, ref_epoch, pmra_masyr, pmdec_masyr, parallax_mas, "
                "gaia_source_id, note) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "on conflict (name) do update set kind=excluded.kind, ra_deg=excluded.ra_deg, dec_deg=excluded.dec_deg, "
                "ref_epoch=excluded.ref_epoch, pmra_masyr=excluded.pmra_masyr, pmdec_masyr=excluded.pmdec_masyr, "
                "parallax_mas=excluded.parallax_mas, gaia_source_id=excluded.gaia_source_id, note=excluded.note returning id",
                (src["name"], src["kind"], src["ra"], src["dec"], src["epoch"], src["pmra"], src["pmdec"], src["parallax"],
                 int(src["gaia_source_id"]) if src["gaia_source_id"] else None, src["note"]),
            ).fetchone()[0]
            conn.execute("delete from detections where source_id = %s and method = any(%s)", (sid, PIPELINE_METHODS))
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into detections (source_id, collection, obs_id, detector, mjd, ra_deg, dec_deg, pos_err_arcsec, "
                    "wave_um, flux_ujy, flux_err_ujy, flags, method, east_arcsec, north_arcsec, snr, n_exposures, used, rel_flux) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(sid, d.get("collection") or "spherex", d["obs_id"], d["detector"], d["mjd"], d["ra"], d["dec"],
                      d["pos_err_arcsec"], d["wave_um"], d["flux_ujy"], d["flux_err_ujy"], d["flags"], d["method"], d["east"],
                      d["north"], d["snr"], d["n_exposures"], d["used"], d["rel_flux"]) for d in result.get("detections", [])],
                )
            if tr:
                t = result["track"]
                conn.execute(
                    "insert into tracks (source_id, epoch, ra0_deg, dec0_deg, pmra_masyr, pmdec_masyr, pmra_err, pmdec_err, "
                    "n_detections, rms_arcsec, mjd_min, mjd_max, parallax_mas, model, summary) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (source_id) do update set "
                    "epoch=excluded.epoch, ra0_deg=excluded.ra0_deg, dec0_deg=excluded.dec0_deg, pmra_masyr=excluded.pmra_masyr, "
                    "pmdec_masyr=excluded.pmdec_masyr, pmra_err=excluded.pmra_err, pmdec_err=excluded.pmdec_err, "
                    "n_detections=excluded.n_detections, rms_arcsec=excluded.rms_arcsec, mjd_min=excluded.mjd_min, "
                    "mjd_max=excluded.mjd_max, parallax_mas=excluded.parallax_mas, model=excluded.model, "
                    "summary=excluded.summary, updated_at=now()",
                    (sid, t["epoch"], t["ra"], t["dec"], t["pmra"], t["pmdec"], t["pmra_err"], t["pmdec_err"], t["n_detections"],
                     t["rms_arcsec"], t["mjd_min"], t["mjd_max"], t["parallax_mas"], t["model"], json.dumps(tr["summary"])),
                )
            else:
                conn.execute("delete from tracks where source_id = %s", (sid,))
        with _lock:
            _cache.clear()
        return True
    except Exception as exc:  # read-only role, database away: the local copy still serves the viewer
        log.warning("could not write %s to the measurement database: %s: %s", result.get("name"), type(exc).__name__, exc)
        return False


def sync_db() -> int:
    """Write the stored results the team database does not hold yet (measured
    before the database was connected, or while it was away). Returns how
    many were written."""
    if not db_enabled():
        return 0
    n = 0
    for key, payload in _conn().execute("SELECT key, payload FROM results").fetchall():
        result = json.loads(payload)
        if result.get("stats", {}).get("in_database") or not result.get("track"):
            continue  # already there, or nothing was detected: nothing to write
        if not save_db(result):
            break  # the database refuses or is away: the next start tries again
        result.setdefault("stats", {})["in_database"] = True
        save_local(key, result)
        n += 1
    return n


def near(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """Sources within ``radius_deg`` with their fitted track and detections:
    everything in the team database, plus pipeline results it does not hold."""
    key = (round(ra, 5), round(dec, 5), round(radius_deg, 4))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL_S:
            return hit[1]
    local = local_near(ra, dec, radius_deg)
    result = []
    if db_enabled():
        try:
            result = db_near(ra, dec, radius_deg)
        except Exception:
            if not local:
                raise
            log.warning("measurement database not reachable; serving local results only")
    have = {s["name"] for s in result}
    result += [s for s in local if s["name"] not in have]
    result.sort(key=lambda s: s["name"])
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (time.time(), result)
    return result
