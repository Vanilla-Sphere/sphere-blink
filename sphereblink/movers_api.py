"""The blind search for moving objects as a service, and its HTTP endpoints.

    GET  /api/movers                     the Discoveries log (all, or near a position)
    GET  /api/movers/field?ra=..&dec=..  what was found in a progressive field; starts its search
    GET  /api/movers/{id}                one object: path, every detection, brightness per exposure
    POST /api/movers/{id}/vote           a visitor's opinion: real motion or false alarm

A progressive field is searched once all its frames are in the cache: by
itself when the field finishes loading, or when the viewer asks for the
field's objects. The search runs in a process of its own, one field at a
time, reads the frames from the disk cache and writes what it finds to the
local store (``mover_store.py``). Detections of fields searched earlier are
linked together with the new ones, so the log grows with the part of the map
people have opened.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
import re
import secrets
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from fastapi import APIRouter, Body, HTTPException, Query

from . import config, guard, memory, mover_store, movers, tiles
from .cutout import Grid, cache_key

log = logging.getLogger(__name__)
router = APIRouter()

ENABLED = os.environ.get("SPHEREX_MOVER_SEARCH", "1") not in ("0", "false", "no")
MIN_FOV_ARCMIN = 20.0  # smaller views hold too little of a path
MIN_FRAMES = 40
JOB_TIMEOUT_S = 900
TRIALS = 2  # shuffled-time searches that give a field its chance figure
STACK = os.environ.get("SPHEREX_MOVER_STACK", "1") not in ("0", "false", "no")  # faint and slow objects (mover_stack)
STACK_BUDGET_S = float(os.environ.get("SPHEREX_MOVER_STACK_BUDGET_S", "180"))  # past it the four-day windows are skipped
REACH_DEG = 0.6  # how far outside a field stored detections are taken from
MAX_WAITING = 8  # searches waiting; more are left for the next time the field is asked for
RUNNING = ("queued", "searching")
MIN_FREE_MB = float(os.environ.get("SPHEREX_MOVER_MIN_FREE_MB", "1800"))  # a search holds about 1 GB (measured: 984 MB for 713 frames)
WAIT_FOR_ROOM_S = 600
RECHECK_EVERY_S = 600  # how often the catalogues are asked again about objects they did not answer for
REFRESH_AFTER_S = 90  # after a start: fields an earlier version of the search went through are searched again
REFRESH_MIN_CACHED = 0.95  # ... when this share of their frames is still in the disk cache
VOTES_PER_MIN = float(os.environ.get("SPHEREX_RATE_VOTES_PER_MIN", "20"))

_svc = None
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_votes = guard.Buckets(VOTES_PER_MIN / 60.0, VOTES_PER_MIN)
_salt: bytes | None = None
_VOTER = re.compile(r"^[A-Za-z0-9-]{16,64}$")
_ID = re.compile(r"^VS-\d{5}-[A-Z2-9]{4}$")


def attach(svc) -> None:
    """Give the router the service of ``main``; fields that finish loading are searched from now on."""
    global _svc
    _svc = svc
    if ENABLED and "pytest" not in sys.modules and _field_ready not in tiles.listeners:
        tiles.listeners.append(_field_ready)
        threading.Thread(target=_after_start, daemon=True).start()


def _executor() -> ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        return _pool


# ----------------------------------------------------------------------------- the search (runs in a child process)
def _cube(grid: Grid, exposures: list, min_cached: float = 0.0) -> tuple[np.ndarray, list[dict]]:
    """The frames of a field from the disk cache, and what the searches need to know of each."""
    from .service import Store

    store = Store()
    cube = np.empty((len(exposures), grid.npix, grid.npix), dtype=np.float32)
    frames = []
    for e in exposures:
        f = store.get_frame(cache_key(e, grid), e)
        if f is None or f is False:
            continue
        cube[len(frames)] = f.image
        frames.append({"exposure": f"{e.obs_id}:D{e.detector}", "mjd": e.mjd, "detector": e.detector, "wave_um": f.wave_um})
    if len(frames) < max(MIN_FRAMES, min_cached * len(exposures)):
        raise ValueError(f"only {len(frames)} of {len(exposures)} frames of this field are in the cache")
    return cube[: len(frames)], frames


def _polite() -> None:
    """The viewer comes first: the searching process runs at niceness 10 (set once: the process is used again)."""
    try:
        os.nice(max(0, 10 - os.nice(0)))
    except OSError:
        pass


def _shown(tracks: list, names: dict[int, dict]) -> list[str]:
    """What becomes of each track: "shown" (it has a name, or evidence strong enough without one, see
    ``movers.strong``), "dropped" (the catalogues were asked, have no object on it, and it is weaker) or "waiting"
    (weaker, and no service answered: stored but not shown until one does, see ``settle_uncompared``)."""
    out = []
    for i, t in enumerate(tracks):
        m = names.get(i) or {}
        out.append("shown" if m.get("name") or movers.strong(t.extra) else "dropped" if m.get("checked") else "waiting")
    return out


def _names(tracks: list) -> dict[int, dict]:
    try:
        from . import known_objects

        return known_objects.identify(tracks)
    except Exception:  # a name is an extra: the object is stored without one
        log.exception("could not look the objects up among the known ones")
        return {}


def run(grid: Grid, exposures: list, known: list[dict], trials: int = TRIALS, min_cached: float = 0.0) -> dict:
    """Search one field whose frames are in the disk cache."""
    _polite()
    t0 = time.time()
    cube, frames = _cube(grid, exposures, min_cached)
    result = movers.search(cube, frames, grid.wcs, grid.scale_arcsec, known=[movers.Detection(**d) for d in known],
                           trials=trials)
    result["stats"].update(seconds=round(time.time() - t0, 1), exposures=len(exposures), known=len(known))
    # A path without a name is shown only on stronger evidence (movers.strong). One the catalogues were asked about
    # and have no object on is dropped when it is weaker. One that could not be compared (no service answered) and
    # is weaker is stored but not shown, until a service answers (see settle_uncompared).
    names = _names(result["tracks"])
    states = _shown(result["tracks"], names)
    keep = [i for i, s in enumerate(states) if s != "dropped"]
    result["stats"].update(unnamed_dropped=states.count("dropped"), waiting=states.count("waiting"), tracks=states.count("shown"))
    result["tracks"], names = [result["tracks"][i] for i in keep], {k: names[i] for k, i in enumerate(keep) if i in names}
    ids = mover_store.save_field(grid.key(), grid.ra, grid.dec, grid.fov_arcmin, len(exposures), result, names,
                                 pending={k for k, i in enumerate(keep) if states[i] == "waiting"})
    return {"ids": ids, "stats": result["stats"]}


def run_stack(grid: Grid, exposures: list, min_cached: float = 0.0) -> dict:
    """The second search of a field, for what no single frame shows: faint and slow objects (``mover_stack``).

    It runs after the linker's results are stored and shown, because it takes minutes; what it finds is added
    to them. The same rules decide what is shown: a name, or evidence strong enough without one, which for a
    stacked path is its own level of 7.5 sigma. Below it chance gives a path in every second field.
    """
    _polite()
    t0 = time.time()
    key = grid.key()
    cube, frames = _cube(grid, exposures, min_cached)
    sky = movers.static_sky(cube, [int(f.get("detector") or 0) for f in frames])
    tracks, stats = stacked(cube, frames, grid, sky, mover_store.field_tracks(key))
    if stats.get("error"):
        return {"ids": [], "stats": stats}  # not noted in the store: tried again after the next start
    names = _names(tracks)
    states = _shown(tracks, names)
    keep = [i for i, s in enumerate(states) if s != "dropped"]
    stats.update(unnamed_dropped=states.count("dropped"), waiting=states.count("waiting"), tracks=states.count("shown"),
                 seconds=round(time.time() - t0, 1))
    ids = mover_store.add_tracks(key, [tracks[i] for i in keep], {k: names[i] for k, i in enumerate(keep) if i in names},
                                 pending={k for k, i in enumerate(keep) if states[i] == "waiting"}, stats=stats)
    return {"ids": ids, "stats": stats}


def stacked(cube, frames: list[dict], grid: Grid, sky, tracks: list) -> tuple[list, dict]:
    """The faint and slow objects of the field, which no single frame shows
    (``mover_stack``): tracks beside the linker's, and what the search says
    of itself. It is an addition: when it fails, the field keeps what the
    linker found."""
    try:
        from . import mover_stack

        out = mover_stack.search(cube, frames, grid.wcs, grid.scale_arcsec, sky=sky, exclude=tracks, budget_s=STACK_BUDGET_S)
        st = out["stats"]
        return out["tracks"], {"found": len(out["tracks"]), "seconds": st.get("seconds"), "passes": st.get("passes"),
                               "candidates": st.get("candidates"), "rejected": st.get("rejected"),
                               "skipped_windows": st.get("skipped_windows"), "clear_share": st.get("clear_share"),
                               "version": out.get("version")}
    except Exception as exc:
        log.exception("the search for faint and slow objects failed")
        return [], {"found": 0, "error": type(exc).__name__}


# ----------------------------------------------------------------------------- jobs (in the web process)
free_mb = memory.free_mb  # what this container may still take, the file cache counted as free


def suitable(grid: Grid) -> bool:
    return grid.fov_arcmin >= MIN_FOV_ARCMIN and grid.scale_arcsec >= config.NATIVE_PIXEL_ARCSEC - 0.01


def _exposures(grid: Grid) -> list:
    exps = _svc.view_exposures(grid)
    if len(exps) > config.MAX_FRAMES:  # the same thinning as the view itself
        step = (len(exps) - 1) / (config.MAX_FRAMES - 1)
        exps = [exps[round(k * step)] for k in range(config.MAX_FRAMES)]
    return exps


def _room() -> None:
    """Wait for a quiet moment: the viewer comes first."""
    waited = 0
    while (room := free_mb()) is not None and room < MIN_FREE_MB:
        if waited >= WAIT_FOR_ROOM_S:
            raise RuntimeError("the server is busy; this field will be searched when it is asked for again")
        time.sleep(10)
        waited += 10


def _work(key: str, grid: Grid, min_cached: float = 0.0, stack: bool = True) -> None:
    job = _jobs[key]
    try:
        exposures = _exposures(grid)
        if mover_store.searched(key, len(exposures)):
            job["status"] = "ready"
        else:
            known = [d.to_dict() for d in mover_store.detections_around(grid.ra, grid.dec, grid.fov_arcmin / 120.0 * 1.42 + REACH_DEG,
                                                                        not_field=key)]
            for d in known:
                d.pop("x", None), d.pop("y", None), d.pop("frame", None)
            _room()
            future = _executor().submit(run, grid, exposures, known, TRIALS, min_cached)
            while not future.running() and not future.done():
                time.sleep(0.5)
            job["status"] = "searching"
            out = future.result(timeout=JOB_TIMEOUT_S)
            job.update(status="ready", stats=out["stats"])
            log.info("movers: field %s searched in %.0f s: %s", key, out["stats"].get("seconds", 0), out["ids"])
            if out["stats"].get("waiting"):
                settle_uncompared()
    except Exception as exc:
        log.exception("mover search of %s failed", key)
        job.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        job["finished"] = time.time()
    if job["status"] == "ready" and STACK and stack:
        _work_stack(key, grid, exposures, min_cached)


def _work_stack(key: str, grid: Grid, exposures: list, min_cached: float) -> None:
    """The second search of the field (``run_stack``). The field is "ready" meanwhile: what the linker found is
    shown, and ``job["stack"]`` says where this one is ("searching", "ready" or "error")."""
    job = _jobs[key]
    try:
        from . import mover_stack

        if mover_store.stacked(key, mover_stack.VERSION):
            job["stack"] = "ready"
            return
        job["stack"] = "searching"
        _room()
        out = _executor().submit(run_stack, grid, exposures, min_cached).result(timeout=JOB_TIMEOUT_S)
        if out["stats"].get("error"):
            raise RuntimeError(out["stats"]["error"])
        job.update(stack="ready", stack_stats=out["stats"])
        log.info("movers: field %s searched for faint and slow objects in %.0f s: %s", key, out["stats"].get("seconds", 0), out["ids"])
        if out["stats"].get("waiting"):
            settle_uncompared()
    except Exception as exc:
        log.exception("search for faint and slow objects of %s failed", key)
        job.update(stack="error", stack_error=f"{type(exc).__name__}: {exc}")


def start(grid: Grid, min_cached: float = 0.0, stack: bool = True) -> dict:
    """Search a field unless it is being searched or was searched already. ``stack``: go on with the search for
    faint and slow objects when the linker is through."""
    key = grid.key()
    with _lock:
        job = _jobs.get(key)
        if job and (job["status"] in RUNNING or (job["status"] == "ready" and mover_store.field(key))):
            return job
        if sum(1 for j in _jobs.values() if j["status"] in RUNNING) >= MAX_WAITING:
            return {"status": "busy"}
        if len(_jobs) > 300:
            for k in [k for k, j in _jobs.items() if j["status"] not in RUNNING][:150]:
                _jobs.pop(k, None)
        job = _jobs[key] = {"status": "queued", "started": time.time()}
    threading.Thread(target=_work, args=(key, grid, min_cached, stack), daemon=True).start()
    return job


# ----------------------------------------------------------------------------- keeping the log whole
_rechecked = 0.0


def settle_uncompared(force: bool = False) -> dict:
    """Ask the catalogues again about the objects no service answered for.

    Such an object is shown as "not compared" when its evidence is strong and
    is kept out of sight when it is weaker; here it gets its name, becomes a
    candidate, or is dropped (``mover_store.settle``). At most once per
    ``RECHECK_EVERY_S`` unless forced.
    """
    global _rechecked
    with _lock:
        if not force and time.time() - _rechecked < RECHECK_EVERY_S:
            return {}
        _rechecked = time.time()
    waiting = mover_store.uncompared()
    if not waiting:
        return {}
    from . import known_objects

    # field by field: one question to a service covers one piece of the sky
    fields: dict[str, list] = {}
    for mid, track, payload in waiting:
        fields.setdefault((payload.get("fields") or [mid])[0], []).append((mid, track))
    out: dict[str, int] = {}
    for group in fields.values():
        names = known_objects.identify([t for _, t in group])
        for i, (mid, _) in enumerate(group):
            what = mover_store.settle(mid, names.get(i) or {})
            out[what] = out.get(what, 0) + 1
    log.info("movers: %d objects waited for the catalogues: %s", len(waiting), out)
    return out


def refresh_stale(wait_s: float = 5.0) -> int:
    """Search again the fields an earlier version of the search went through, one at a time, from the frames
    still in the disk cache. Returns how many were started."""
    started = 0
    for f in mover_store.stale_fields():
        try:
            grid = Grid.from_fov(f["ra"], f["dec"], f["fov"])
            if grid.key() != f["key"] or not suitable(grid):
                continue
            # the linker first, in every field: the second search takes minutes and comes after (stack_missing)
            job = start(grid, min_cached=REFRESH_MIN_CACHED, stack=False)
            if job.get("status") == "busy":
                break
            started += 1
            while job.get("status") in RUNNING:  # one at a time: the fields people open now come first
                time.sleep(wait_s)
        except Exception:
            log.exception("could not search field %s again", f["key"])
    return started


def stack_missing() -> int:
    """Search for faint and slow objects in the fields the linker has gone through and this search has not
    (fields searched before it existed, or again by the linker since), one at a time, from the frames still in
    the disk cache. Returns how many were searched."""
    if not STACK:
        return 0
    from . import mover_stack

    done = 0
    for f in mover_store.unstacked_fields(mover_stack.VERSION):
        try:
            grid = Grid.from_fov(f["ra"], f["dec"], f["fov"])
            key = grid.key()
            if key != f["key"] or not suitable(grid):
                continue
            with _lock:
                job = _jobs.get(key)
                if job and (job["status"] in RUNNING or job.get("stack")):  # under way, or tried since the start
                    continue
                job = _jobs[key] = {"status": "ready", "started": time.time(), "finished": time.time()}
            _work_stack(key, grid, _exposures(grid), REFRESH_MIN_CACHED)
            done += job.get("stack") == "ready"
        except Exception:
            log.exception("could not search field %s for faint and slow objects", f["key"])
    return done


def _after_start() -> None:
    time.sleep(REFRESH_AFTER_S)
    try:
        if _svc is not None:
            log.info("movers: %d fields of an earlier search searched again", refresh_stale())
        settle_uncompared(force=True)
        if _svc is not None:
            log.info("movers: %d fields searched for faint and slow objects", stack_missing())
    except Exception:
        log.exception("movers: the check after the start failed")


def _field_ready(grid: Grid) -> None:
    """A progressive field has all its frames: search it."""
    try:
        if _svc is not None and suitable(grid):
            start(grid)
    except Exception:
        log.exception("could not start the mover search of a field")


def _public(job: dict) -> dict:
    out = {k: job[k] for k in ("status", "error", "stats", "stack") if k in job}
    if "started" in job:
        out["elapsed_s"] = round((job.get("finished") or time.time()) - job["started"], 1)
    if job.get("status") == "queued":
        out["waiting"] = sum(1 for j in _jobs.values() if j["status"] in RUNNING)
    return out


def _address() -> str:
    """Where a vote came from, as a hash that cannot be turned back into the address."""
    global _salt
    if _salt is None:
        path = config.CACHE_DIR / "movers.salt"
        try:
            _salt = path.read_bytes()
        except OSError:
            _salt = secrets.token_bytes(32)
            try:
                config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_salt)
            except OSError:
                pass
    return hashlib.sha256(_salt + guard.current().encode()).hexdigest()[:24]


# ----------------------------------------------------------------------------- HTTP
@router.get("/api/movers")
def api_movers(
    ra: float | None = Query(None, ge=0, le=360), dec: float | None = Query(None, ge=-90, le=90),
    radius: float = Query(1.0, gt=0, le=30, description="degrees around ra, dec"),
    ids: str | None = Query(None, max_length=4000, description="comma-separated ids, e.g. the ones a visitor follows"),
    limit: int = Query(200, ge=1, le=1000),
    coverage: bool = Query(False, description="also list the sky cells searched so far"),
):
    """The Discoveries log: every moving object the blind search has found.

    Nothing is known beforehand about these objects: each is a set of
    detections in different exposures that lie on one straight path, which
    the pixels of the other exposures along that path confirm. They are
    candidates, not discoveries: most are known asteroids and comets
    (``known`` names the match), and visitors can say whether a path looks
    real (``votes``). Without ``ra`` and ``dec`` the newest come first.
    """
    if ENABLED and "pytest" not in sys.modules and time.time() - _rechecked >= RECHECK_EVERY_S:
        threading.Thread(target=settle_uncompared, daemon=True).start()
    if ids is not None:
        wanted = [i for i in (s.strip() for s in ids.split(",")) if _ID.match(i)][:500]
        rows = mover_store.listing(ids=wanted, limit=limit)
    elif ra is not None and dec is not None:
        rows = mover_store.listing(ra, dec, radius, limit=limit)
    else:
        rows = mover_store.listing(limit=limit)
    out = {"version": movers.VERSION, "enabled": ENABLED, "total": mover_store.count(), "movers": rows}
    if coverage:
        out["coverage"] = mover_store.coverage()
    return out


@router.get("/api/movers/field")
def api_movers_field(
    ra: float | None = Query(None, ge=0, le=360), dec: float | None = Query(None, ge=-90, le=90),
    levels: int = Query(3, ge=1, le=tiles.MAX_LEVELS, description="rings of the progressive field (3 = 7 x 7 cells)"),
    tile_id: str | None = Query(None, description="seed cell by id instead of a position"),
):
    """Moving objects in one progressive field, and the state of its search.

    The field is searched when all its frames have been loaded (open it with
    ``/api/tiles`` first). ``status`` is ``not_loaded``, ``queued``,
    ``searching``, ``ready`` or ``error``; poll until it is ``ready``.
    ``stats.chance_tracks`` tells how many paths the same search finds when
    the exposure times are shuffled, that is, by chance alone.
    """
    try:
        if tile_id:
            ra, dec = tiles.center(*tiles.parse_tile_id(tile_id))
        if ra is None or dec is None:
            raise ValueError("give ra=&dec= or tile_id=")
        seed = tiles.block(ra, dec, 0)[0]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    grid = tiles.stage_grid(seed["ra_c"], seed["dec_c"], levels)
    key = grid.key()
    stored = mover_store.field(key)
    job = _jobs.get(key)
    if ENABLED and _svc is not None and not (job and job["status"] in RUNNING):
        view = _svc.peek(grid)
        if view is not None and view.status == "ready" and not (stored and stored["version"] == movers.VERSION
                                                                and (not STACK or (stored["stats"].get("stack") or job))):
            job = start(grid)
    if stored and stored["version"] != movers.VERSION:
        stored = None  # what an earlier version of the search stored is not shown, and is not "ready"
    status = job["status"] if job and job["status"] != "ready" else "ready" if stored else "not_loaded"
    out = {"field": {"ra": grid.ra, "dec": grid.dec, "fov": round(grid.fov_arcmin, 3), "levels": levels, "seed": seed["tile_id"]},
           "status": status, "job": _public(job) if job else None, "enabled": ENABLED}
    if stored:
        out["searched"] = stored["searched"]
        out["stats"] = stored["stats"]
    out["movers"] = mover_store.listing(grid.ra, grid.dec, grid.fov_arcmin / 120.0 * 1.42, limit=500)
    return out


@router.get("/api/movers/{mover_id}")
def api_mover(mover_id: str, voter: str | None = Query(None, description="the caller's browser id, to learn its own vote")):
    """One moving object: the fitted path, its detections, and the brightness
    measured at the predicted place in every exposure along the path."""
    if not _ID.match(mover_id):
        raise HTTPException(404, "no such object")
    out = mover_store.get(mover_id, voter if voter and _VOTER.match(voter) else "")
    if out is None:
        raise HTTPException(404, "no such object")
    return out


@router.post("/api/movers/{mover_id}/vote")
def api_mover_vote(mover_id: str, body: dict = Body(...)):
    """Say whether an object's motion looks real. Body: ``{"voter": "<browser id>", "vote": "real" | "false" | null}``.

    One voice per browser and object; sending another vote changes it,
    ``null`` takes it back. No account is needed: the browser makes up a
    random id once and keeps it.
    """
    if not _ID.match(mover_id):
        raise HTTPException(404, "no such object")
    voter, value = body.get("voter"), body.get("vote")
    if not isinstance(voter, str) or not _VOTER.match(voter):
        raise HTTPException(400, "voter must be the browser's random id (16 to 64 letters, digits or dashes)")
    if value is not None and value not in mover_store.VOTES:
        raise HTTPException(400, "vote must be 'real', 'false' or null")
    guard.charge(_votes, "votes")
    try:
        tally = mover_store.vote(mover_id, voter, value, _address())
    except KeyError:
        raise HTTPException(404, "no such object") from None
    except PermissionError as exc:
        raise HTTPException(429, str(exc)) from exc
    return {"id": mover_id, "votes": tally, "my_vote": value}
