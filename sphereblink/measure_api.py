"""Measurement jobs and their HTTP endpoints.

    GET /api/measure?target=Barnard's Star      start (or read) a measurement
    GET /api/measure?ra=..&dec=..               the same for any position
    GET /api/measured                           everything measured so far

A job finds the exposures, then measures in a separate process (so the web
server keeps answering), stores the result on disk and, when the database
role allows it, in the team database. The five challenge targets are measured
by themselves shortly after the server starts, and again when the pipeline
changes or new exposures may have arrived; what visitors had measured is
measured again when the pipeline changes.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from . import config, measure, measurements, refcat
from .resolve import FEATURED, ResolverUnavailable, Target, resolve

log = logging.getLogger(__name__)
router = APIRouter()

VERSION = 3  # raise to have every stored result measured again
MAX_AGE_S = 7 * 24 * 3600  # challenge targets are measured again after a week (new exposures)
JOB_TIMEOUT_S = 600
MID_MJD = 61000.0  # a date inside the SPHEREx data, for cone searches

_svc = None
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None


def attach(svc) -> None:
    """Give the router the service of ``main`` and start the self-measurement
    of the challenge targets (not under pytest, not with SPHEREX_AUTO_MEASURE=0)."""
    global _svc
    _svc = svc
    if os.environ.get("SPHEREX_AUTO_MEASURE", "1") != "0" and "pytest" not in sys.modules:
        threading.Thread(target=_measure_featured, daemon=True).start()


def _executor() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        return _pool


# ----------------------------------------------------------------------------- the job (runs in a child process)
def run(target: Target, exposures: list, wave_maps: dict, concurrency: int = 16) -> dict:
    """Measure one target. Everything slow happens here: the Gaia query, the
    stamps from S3, the fits."""
    t0 = time.time()
    key = refcat.featured_key(target.name)
    try:
        field = refcat.gaia_cone(target.ra, target.dec, 4.5 / 60.0)
        gaia_ok = True
    except Exception:  # without Gaia: no neighbour stars, no pointing check, still a measurement
        field, gaia_ok = [], False
    body, kind = refcat.body_for(target, field, MID_MJD)
    stamps, failed = asyncio.run(measure.fetch_stamps(exposures, body, concurrency))
    result = measure.measure_all(stamps, body, field, wave_maps)
    ra, dec = body.at(MID_MJD)
    note = FEATURED[key]["note"] if key else ""
    result.update(
        name=body.name, kind=kind, ra=ra, dec=dec, note=note, version=VERSION, measured_at=time.time(), query=asdict(target),
        stats={**result["stats"], "exposures": len(exposures), "failed": failed, "gaia": gaia_ok, "seconds": round(time.time() - t0, 1)},
    )
    return result


# ----------------------------------------------------------------------------- jobs (in the web process)
def job_key(target: Target) -> str:
    return refcat.featured_key(target.name) or f"pos_{target.ra:.4f}_{target.dec:+.4f}"


def _work(key: str, target: Target) -> None:
    job = _jobs[key]
    try:
        job["status"] = "searching"
        exposures = _svc.exposures(target.ra, target.dec)
        if not exposures:
            raise ValueError("no SPHEREx exposure covers this position")
        if len(exposures) > config.MAX_FRAMES:  # deep fields: thin evenly in time
            step = (len(exposures) - 1) / (config.MAX_FRAMES - 1)
            exposures = [exposures[round(k * step)] for k in range(config.MAX_FRAMES)]
        job["exposures"] = len(exposures)
        firsts = {f"{e.collection}_D{e.detector}": e for e in exposures}
        wave_maps = {k: _svc.wave_map(e) for k, e in firsts.items()}
        job["status"] = "measuring"
        result = _executor().submit(run, target, exposures, wave_maps).result(timeout=JOB_TIMEOUT_S)
        measurements.save_local(key, result)
        result["stats"]["in_database"] = measurements.save_db(result)
        measurements.save_local(key, result)
        job["status"] = "ready"
    except Exception as exc:
        log.exception("measurement of %s failed", key)
        job.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        job["finished"] = time.time()


def start(target: Target, refresh: bool = False) -> tuple[str, dict]:
    """Start measuring a target unless it is running or already stored."""
    key = job_key(target)
    with _lock:
        job = _jobs.get(key)
        if job and job["status"] in ("queued", "searching", "measuring"):
            return key, job
        if not refresh and measurements.load_local(key) is not None:
            return key, {"status": "ready"}
        job = _jobs[key] = {"status": "queued", "started": time.time(), "name": target.name}
    threading.Thread(target=_work, args=(key, target), daemon=True).start()
    return key, job


def _stale(result: dict | None) -> bool:
    return result is None or result.get("version") != VERSION or time.time() - result.get("measured_at", 0) > MAX_AGE_S


def _measure_featured() -> None:
    """Keep the challenge targets measured, one after another."""
    time.sleep(float(os.environ.get("SPHEREX_AUTO_MEASURE_DELAY", "40")))
    try:
        measurements.sync_db()  # results measured while there was no database
    except Exception:
        log.exception("could not bring the measurement database up to date")
    for key in refcat.TARGETS:
        try:
            if not _stale(measurements.load_local(key)):
                continue
            _, job = start(resolve(key), refresh=True)
            while job["status"] in ("queued", "searching", "measuring"):
                time.sleep(2.0)
        except Exception:
            log.exception("automatic measurement of %s failed", key)
    try:
        _refresh_stored()
    except Exception:
        log.exception("could not bring the stored measurements up to date")


def _target_of(result: dict) -> Target:
    """The request a stored result answers. Results stored before the request
    was kept are asked for again by their name."""
    try:
        return Target(**result["query"])
    except (KeyError, TypeError):
        return resolve(result["name"])


def _refresh_stored() -> None:
    """Measure again what an older version of the pipeline measured for a
    visitor. A result that can no longer be asked for (its coordinates are
    refused now) is dropped, and so is one that turns out to hold no
    detection: nobody is waiting for it."""
    for row in measurements.list_local():
        key = row["key"]
        if key in refcat.TARGETS or not _stale(row):
            continue
        try:
            target = _target_of(row)
        except ResolverUnavailable:
            continue  # the name services are away: the next start tries again
        except ValueError:
            measurements.delete_local(key)
            continue
        try:
            new_key, job = start(target, refresh=True)
            while job["status"] in ("queued", "searching", "measuring"):
                time.sleep(2.0)
        except Exception:
            log.exception("measuring %s again failed", key)
            continue
        if job["status"] != "ready":
            continue
        if new_key != key:
            measurements.delete_local(key)
        result = measurements.load_local(new_key)
        if result is not None and not result.get("track"):
            measurements.delete_local(new_key)


def _public(job: dict) -> dict:
    out = {k: job[k] for k in ("status", "name", "exposures", "error") if k in job}
    if "started" in job:
        out["elapsed_s"] = round((job.get("finished") or time.time()) - job["started"], 1)
    return out


# ----------------------------------------------------------------------------- HTTP
@router.get("/api/measure")
def api_measure(
    target: str | None = None, ra: float | None = None, dec: float | None = None,
    refresh: bool = Query(False, description="measure again even when a result is stored"),
    detections: bool = Query(True, description="include the position in every exposure"),
):
    """Measure where an object is in every SPHEREx exposure and fit its motion.

    The object is found on the native detector pixels of each exposure (PSF
    fit; exposures of one sky pass are added up first when it is too faint),
    compared with where Gaia or the literature says it should be on that
    date, and a straight-line motion with parallax is fitted. Variable
    objects also get a light curve. The first call starts the job and
    answers ``status: queued``; ask again until ``status`` is ``ready``.
    """
    if _svc is None:
        raise HTTPException(503, "measurement service not started")
    try:
        if ra is not None and dec is not None:
            t = resolve(f"{ra} {dec}")
            if target:
                t.name = target
        elif target:
            t = resolve(target.strip())
        else:
            raise ValueError("give target=<name or coordinates> or ra=&dec=")
    except ResolverUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    key, job = start(t, refresh)
    out = {"key": key, "target": asdict(t), "job": _public(job), "status": job["status"]}
    if job["status"] == "ready":
        result = measurements.load_local(key)
        if result is not None and not detections:
            result.pop("detections", None)
        out["result"] = result
    return out


@router.get("/api/measured")
def api_measured():
    """Every object the pipeline has measured: track, light curve and numbers, without the single positions."""
    running = {k: _public(j) for k, j in _jobs.items() if j["status"] in ("queued", "searching", "measuring")}
    return {"version": VERSION, "database": measurements.db_enabled(), "running": running, "results": measurements.list_local()}
