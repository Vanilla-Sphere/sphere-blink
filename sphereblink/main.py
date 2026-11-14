"""HTTP API + static web viewer.

Every view is addressed by URL parameters, so any image or movie can be
embedded elsewhere, e.g.
    /api/movie.gif?target=barnard&fov=8&det=all&group=day
    /?target=WISE%200855&fov=8
"""

from __future__ import annotations

import gzip
import json
import os
import signal
import struct
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import anyio.to_thread
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import atlas, catalogs, config, distance, guard, measure_api, measurements, movers_api, render, settings, tiles, wings
from . import resolve as resolver
from .cutout import Grid
from .resolve import FEATURED, NameNotFound, ResolverUnavailable, Target, resolve
from .service import Service

WEB = Path(__file__).resolve().parent.parent / "web"
CUBE_BUDGET = 40_000_000  # max pixels (epochs * npix^2) sent to a browser at once

app = FastAPI(title="Vanilla Sphere", version="0.2.0", description=__doc__)
app.add_middleware(GZipMiddleware, minimum_size=2000, compresslevel=4)
# The API only reads: any site may show its pictures and fetch its data
# (no cookies, no credentials), and nothing but GET is offered.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "HEAD"], expose_headers=["X-Job", "X-Atlas", "Retry-After"])
app.add_middleware(guard.Guard)  # outermost: caller, request limits, security headers on every answer
svc = Service()
fields = tiles.Fields(svc)
measure_api.attach(svc)  # measurement pipeline: /api/measure, /api/measured
app.include_router(measure_api.router)
movers_api.attach(svc)  # blind search for moving objects: /api/movers
app.include_router(movers_api.router)
# The usual views of the featured targets are fetched once and then stay in
# the disk cache whatever else is opened: they open without the archive.
FEATURED_GRIDS = [Grid.from_fov(t.ra, t.dec, 8.0) for t in (resolve(k) for k in FEATURED)]
svc.pinned = {g.key() for g in FEATURED_GRIDS}
sky = atlas.Atlas()  # the photograph behind the sky map: tiles of the 2MASS atlas, kept on disk
_closing = threading.Event()


@app.exception_handler(guard.Busy)
async def _busy(request: Request, exc: guard.Busy):
    guard.log.warning("guard: refused %s (%s)", guard.current(), exc.detail)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status,
                        headers={"Retry-After": str(int(exc.retry_after + 0.999)), "Cache-Control": "no-store"})


@app.on_event("startup")
async def _warm_up() -> None:
    # Requests are answered in threads that mostly wait (for the archive, a
    # name service, a view being gathered): allow many more than the default 40.
    anyio.to_thread.current_default_thread_limiter().total_tokens = config.REQUEST_THREADS
    # The limits of the pipeline and of the callers are read from the team
    # database from now on, twice a minute (settings.py).
    settings.on_change(svc.settings_changed)
    if "pytest" not in sys.modules:
        settings.start(_closing)

    def warm() -> None:
        svc.warm_up()
        if os.environ.get("SPHEREX_KEEP_FEATURED", "1") != "0" and "pytest" not in sys.modules:
            svc.keep_ready(FEATURED_GRIDS)

    threading.Thread(target=warm, daemon=True).start()
    if config.WARM_CATALOGS:
        threading.Thread(target=_warm_catalogs, daemon=True).start()
    sky.start(_closing, seed=config.ATLAS_SEED and "pytest" not in sys.modules)
    _stop_fast()


def _stop_children() -> None:
    import multiprocessing

    for child in multiprocessing.active_children():  # the fetch and measurement processes
        try:
            child.kill()
        except Exception:
            pass


def _exit_now() -> None:
    _stop_children()
    os._exit(0)


def _leave(after: float) -> None:
    if "pytest" not in sys.modules:
        t = threading.Timer(after, _exit_now)
        t.daemon = True
        t.start()


def _stop_fast() -> None:
    """A deploy stops this server before the next one may start (they share
    one disk), so every second spent stopping is a second of downtime, and
    the host kills a server that has not left after its grace period (30 s
    by default, which is what a deploy used to cost). On the stop signal
    the requests that only wait are let go at once; the process then leaves
    without waiting for its worker processes. The caches on disk are safe
    at any moment (SQLite with a write-ahead log)."""
    try:
        previous = signal.getsignal(signal.SIGTERM)

        def on_term(signum, frame):
            _closing.set()
            _leave(8.0)  # whatever else happens
            if callable(previous):
                previous(signum, frame)

        signal.signal(signal.SIGTERM, on_term)
    except ValueError:  # not the main thread (tests, embedding): the shutdown hook below still runs
        pass


@app.on_event("shutdown")
def _shut_down() -> None:
    _closing.set()
    if "pytest" not in sys.modules:
        _stop_children()  # they would outlive a server that ends before the timer below
    _leave(0.3)


def _catalog(cat: str, grid: Grid, cached_only: bool = False) -> list[dict] | None:
    """Overlay catalogue of a view, from the store or from the catalogue services."""
    key = f"{cat}_v3_{grid.key()}"
    sources = svc.store.get_catalog(key)
    if sources is None and not cached_only:
        if cat not in catalogs.QUERIES:
            raise ValueError("cat must be gaia or catwise")
        guard.charge(guard.catalogs, "catalogue queries")  # only a query that goes out counts against the caller
        sources = catalogs.query(cat, grid.ra, grid.dec, grid.fov_arcmin / 60.0 * 0.75)
        svc.store.put_catalog(key, sources)
    return sources


FIELD_FOVS = (8.0, 4.0, 15.0, 30.0)  # the field sizes the viewer offers, the default first
FIELD_LEVELS = (1, 3)  # and its two progressive fields


def featured_grids() -> list[Grid]:
    """Every view of a featured target the viewer can ask an overlay for: the
    plain fields centred on the target and the stages of the progressive
    fields, built the way a request for them is."""
    grids = []
    for fov in FIELD_FOVS:
        for key in FEATURED:
            t = resolve(key)
            grids.append(Grid.from_fov(t.ra, t.dec, fov))
    for key in FEATURED:
        t = resolve(key)
        ra_c, dec_c = tiles.center(*tiles.cell_of(t.ra, t.dec))
        for level in sorted({lv for n in FIELD_LEVELS for lv in tiles.stage_levels(n)}):
            g = tiles.stage_grid(ra_c, dec_c, level)
            grids.append(Grid.from_fov(g.ra, g.dec, round((2 * level + 1) * tiles.STEP * 60.0, 3)))
    return grids


def _warm_catalogs(rounds: int = 40, pause: float = 90.0) -> None:
    """Fill the overlay catalogues of the featured targets after a start, so
    switching an overlay on never waits for a catalogue service. What is
    already stored is skipped; what a service refused is tried again later."""
    for _ in range(rounds):
        missing = 0
        for grid in featured_grids():
            for cat in ("gaia", "catwise"):
                try:
                    _catalog(cat, grid)
                except Exception:
                    missing += 1
        if not missing:
            return
        time.sleep(pause)


# --------------------------------------------------------------------------- helpers
_unknown_names: dict[str, tuple[float, str]] = {}  # name -> (when asked, the services' answer)
UNKNOWN_NAME_TTL = 900.0  # seconds a "no such object" answer is repeated without asking again


_names: "OrderedDict[str, Target]" = OrderedDict()  # names resolved since the start
_name_locks: dict[str, threading.Lock] = {}
_names_lock = threading.Lock()


def _resolve_cached(text: str) -> Target:
    """Resolve a name once. Coordinates and the featured targets need no
    service. Anything else is looked up at the name services, which takes
    seconds: the answer is kept in memory and on disk, and "no such object"
    is remembered for a while too, so a typo typed twice, or polled by the
    page, answers at once. Only a real lookup counts against the caller."""
    text = " ".join(text.split())
    key = text.lower()
    seen = _unknown_names.get(key)
    if seen and time.time() - seen[0] < UNKNOWN_NAME_TTL:
        raise NameNotFound(seen[1])
    with _names_lock:
        hit = _names.get(key)
        if hit is not None:
            _names.move_to_end(key)
            return Target(**hit.to_dict())
        lock = _name_locks.setdefault(key, threading.Lock())
        if len(_name_locks) > 4000:
            _name_locks.clear()
    with lock:  # the same name asked twice at once is looked up once
        with _names_lock:
            hit = _names.get(key)
        if hit is None and key not in FEATURED and key not in resolver.ALIASES and resolver.parse_coords(text) is None:
            stored = svc.store.get_name(key, config.NAME_TTL_DAYS * 86400)
            if stored is not None:
                try:
                    hit = Target(**stored)
                except TypeError:
                    hit = None
            if hit is None:
                guard.charge(guard.names, "name lookups")
                try:
                    hit = resolve(text)
                except NameNotFound as exc:
                    if len(_unknown_names) > 2000:
                        _unknown_names.clear()
                    _unknown_names[key] = (time.time(), str(exc))
                    raise
                except ResolverUnavailable:
                    old = svc.store.get_name(key, float("inf"))  # the services are away: an old answer will do
                    if old is None:
                        raise
                    hit = Target(**old)
                if hit.source != "coordinates":
                    svc.store.put_name(key, hit.to_dict())
        elif hit is None:
            hit = resolve(text)
        with _names_lock:
            _names[key] = hit
            while len(_names) > 2000:
                _names.popitem(last=False)
    return Target(**hit.to_dict())


def _target(target: str | None, ra: float | None, dec: float | None) -> Target:
    try:
        if ra is not None and dec is not None:
            t = resolver.at(ra, dec)
            label = resolver.clean_name(target or "")
            if label:
                t.name = label
            return t
        if target:
            return _resolve_cached(target.strip())
        raise ValueError("give target=<name or coordinates> or ra=&dec=")
    except ResolverUnavailable as exc:  # the name services are down: not the caller's mistake
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


measure_api._resolve = _resolve_cached  # names asked of /api/measure: the same cache and allowance


def _dets(det: str) -> list[int] | None:
    if not det or det.lower() == "all":
        return None
    try:
        ds = sorted({int(x.strip().lstrip("Dd")) for x in det.split(",") if x.strip()})
    except ValueError as exc:
        raise HTTPException(400, "det must be 'rgb', 'all' or a list like 1,2,6") from exc
    if not ds or any(d < 1 or d > 6 for d in ds):
        raise HTTPException(400, "detectors are 1..6")
    return ds


def _band(det: str, band: str | None) -> str:
    """``band=RGB|D1..D6`` is accepted as another spelling of ``det``."""
    return band if band else det


def _view(target, ra, dec, fov) -> tuple[Target, Grid]:
    t = _target(target, ra, dec)
    return t, Grid.from_fov(t.ra, t.dec, fov)


def _frames(grid: Grid, wait: float, partial: bool = False):
    """Frames of a view. Returns (frames | None, job)."""
    job = svc.start(grid)
    t0 = time.time()
    # A view that was dropped from memory (or belongs to a server that has just
    # restarted) is back from the disk cache within a moment: wait for that
    # instead of answering with an empty partial result.
    while (job.status in ("queued", "searching") or job.resuming) and time.time() - t0 < max(wait, 6.0) and not _closing.is_set():
        time.sleep(0.05)
    while job.status not in ("ready", "error") and time.time() - t0 < wait and not _closing.is_set():
        time.sleep(0.2)
    if job.status == "error":
        # the job says it in a sentence fit for the page (service.say); not kept, so asking again tries again
        raise HTTPException(502, job.error, headers={"Cache-Control": "no-store"})
    if job.status != "ready" and not partial:
        return None, job
    return job.snapshot(), job


_epoch_cache: "OrderedDict[tuple, list]" = OrderedDict()
_epoch_lock = threading.Lock()
EPOCH_CACHE_BYTES = 300e6


def _epochs(frames, det, group, diff=False, grid: Grid | None = None):
    if group not in ("exposure", "day", "visit"):
        raise HTTPException(400, "group must be exposure, day or visit")
    # Stacking the frames of a wide mosaic takes seconds, and the page asks for
    # the same epochs several times (cube, picture, movie): keep the last few.
    key = (grid.key(), len(frames), det.strip().lower(), group, diff) if grid is not None and frames else None
    if key is not None:
        with _epoch_lock:
            if key in _epoch_cache:
                _epoch_cache.move_to_end(key)
                return _epoch_cache[key]
    if det.strip().lower() == "rgb":
        eps = render.build_color_epochs(frames, group)
    else:
        eps = render.build_epochs(frames, _dets(det), group)
    if diff:
        eps = render.difference(eps)
    if key is not None:
        with _epoch_lock:
            _epoch_cache[key] = eps
            size = lambda v: sum(e.image.nbytes for e in v)  # noqa: E731
            while len(_epoch_cache) > 1 and sum(size(v) for v in _epoch_cache.values()) > EPOCH_CACHE_BYTES:
                _epoch_cache.popitem(last=False)
    return eps


def _pending(job):
    return JSONResponse(status_code=202, content={"pending": True, "job": job.public()})


FOV = Query(8.0, gt=0, le=config.MAX_FOV_ARCMIN, description="field of view, arcmin")
DET = Query("rgb", description="'rgb' (false colour: R=D6/D5, G=D4/D3, B=D2/D1), 'all', or detectors like 1,2,6")
BAND = Query(None, description="same as det, spelled RGB or D1..D6")


# --------------------------------------------------------------------------- API
# Views in progress live in this process, so the app must run as exactly one
# web process. The id lets a deploy check that: it has to be the same on
# every answer.
INSTANCE = f"{os.getpid()}-{int(time.time())}"


@app.get("/api/health")
async def health():
    return {"ok": True, "collections": config.COLLECTIONS, "instance": INSTANCE}


@app.get("/api/settings")
async def api_settings():
    """The limits in force: how many jobs the pipeline works on at a time and
    how much one caller may ask for. Each with where its value comes from
    (the table ``settings`` of the team database, or the default), the range
    the table may set and, for a row that was not taken as written, why.
    ``read_seconds_ago`` says when the table was read last; a change to it is
    in force within ``refresh_s`` seconds."""
    return JSONResponse(settings.status(), headers={"Cache-Control": "no-store"})


@app.get("/api/featured")
async def featured():
    return JSONResponse([{"key": k, "name": v["name"], "note": v["note"]} for k, v in FEATURED.items()],
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/resolve")
def api_resolve(q: str):
    return _target(q, None, None).to_dict()


@app.get("/api/exposures")
def api_exposures(target: str | None = None, ra: float | None = None, dec: float | None = None):
    """Every SPHEREx Level 2 exposure covering the position (with S3 keys)."""
    t = _target(target, ra, dec)
    guard.charge(guard.catalogs, "archive queries")
    exps = svc.exposures(t.ra, t.dec)
    return {"target": t.to_dict(), "count": len(exps), "exposures": [e.to_dict() for e in exps]}


@app.get("/api/view")
def api_view(target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV):
    """Start gathering all frames for a view; poll until job.status == ready."""
    t, grid = _view(target, ra, dec, fov)
    job = svc.start(grid)
    return {"target": t.to_dict(), "grid": grid.to_dict(), "job": job.public()}


@app.get("/api/tiles")
def api_tiles(
    target: str | None = None, ra: float | None = None, dec: float | None = None,
    levels: int = Query(3, ge=0, le=tiles.MAX_LEVELS, description="rings of cells around the seed cell (3 = 7 x 7 cells)"),
    neighbors: int | None = Query(None, ge=0, le=tiles.MAX_LEVELS, description="same as levels"),
    tile_id: str | None = Query(None, description="seed cell by id instead of a position, e.g. icrs012-v1-2245-0789"),
):
    """A progressive field on the fixed 0.12 degree cell grid (icrs012-v1).

    Starts loading the seed cell, then its first ring, then the whole block of
    (2 x levels + 1)^2 cells, and reports how far it is. Each stage is an
    ordinary view (``ra``, ``dec``, ``fov`` in ``stages``), so every other
    endpoint - cube, frame.png, movie.gif, epochs - works on it. Poll until
    ``status`` is ``ready``.
    """
    try:
        if tile_id:
            ra, dec = tiles.center(*tiles.parse_tile_id(tile_id))
            target = None
        t = _target(target, ra, dec)
        field = fields.start(t.ra, t.dec, neighbors if neighbors is not None else levels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"target": t.to_dict(), **field.public()}


@app.get("/api/cube")
def api_cube(
    request: Request,
    target: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    fov: float = FOV,
    det: str = DET,
    group: str = "day",
    band: str | None = BAND,
    bin: int = Query(1, ge=1, le=4, description="average bin x bin pixels (overview of a wide mosaic)"),
):
    """All epochs of a view in one binary blob for the interactive viewer.

    Layout: uint32 LE json length, json (utf-8, space padded to an even
    offset), then uint16 LE pixels [epoch, row(south->north), col(east->west)].
    While the job is still fetching, the cube contains what has arrived so far.
    """
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, 0, partial=True)
    eps = render.binned(_epochs(frames, _band(det, band), group, grid=grid), bin)
    if eps and len(eps) * eps[0].planes * eps[0].image.shape[-1] ** 2 > CUBE_BUDGET:
        raise HTTPException(413, "too many pixels: reduce the field of view, group by day/visit, or pick one detector")
    blob, enc = render.encode_cube(eps)
    g = grid.to_dict()
    if eps and bin > 1:
        g.update(npix=int(eps[0].image.shape[-1]), scale_arcsec=grid.scale_arcsec * bin, native_npix=grid.npix)
    meta = {
        "target": t.to_dict(),
        "grid": g,
        "job": job.public(),
        "encoding": enc,
        "epochs": [e.meta() for e in eps],
    }
    js = json.dumps(meta).encode()
    js += b" " * ((4 + len(js)) % 2)
    body = struct.pack("<I", len(js)) + js + blob
    # A finished view does not change (until the archive adds exposures): the
    # browser may keep its cube for a while, so going back to a view, or
    # switching bands back and forth, does not download megabytes again.
    headers = {"X-Job": job.status, "Vary": "Accept-Encoding",
               "Cache-Control": "private, max-age=600" if job.status == "ready" and not job.failed else "no-store"}
    if "gzip" in request.headers.get("accept-encoding", "").lower() and len(body) > 2000:
        # Compressed here, in this request's thread (zlib works outside the
        # interpreter lock), not by the middleware on the server's one event
        # loop, where tens of megabytes would hold up every other answer.
        body = gzip.compress(body, compresslevel=3 if len(body) > 20e6 else 4)
        headers["Content-Encoding"] = "gzip"
    return Response(body, media_type="application/octet-stream", headers=headers)


@app.get("/api/epochs")
def api_epochs(
    target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
    det: str = DET, group: str = "day", wait: float = Query(0.0, ge=0, le=60), band: str | None = BAND,
):
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, wait)
    if frames is None:
        return _pending(job)
    eps = _epochs(frames, _band(det, band), group, grid=grid)
    return {"target": t.to_dict(), "grid": grid.to_dict(), "job": job.public(), "epochs": [e.meta() for e in eps]}


@app.get("/api/frame.png")
def api_frame(
    target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
    det: str = DET, group: str = "day", diff: bool = False, i: int = -1,
    size: int = Query(480, ge=128, le=1024), wait: float = Query(55.0, ge=0, le=120), band: str | None = BAND,
):
    """One epoch as a PNG (i = epoch index, negative counts from the end)."""
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, wait)
    if frames is None:
        return _pending(job)
    eps = _epochs(frames, _band(det, band), group, diff, grid=grid)
    if not eps:
        raise HTTPException(404, "no SPHEREx data at this position")
    i = max(0, min(i, len(eps) - 1)) if i >= 0 else max(0, len(eps) + i)
    im = render.frame_image(eps[i], render.shared_limits(eps, diff), size=size, diff=diff, title=t.name)
    return Response(render.png_bytes(im), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/movie.gif")
def api_movie(
    target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
    det: str = DET, group: str = "day", diff: bool = False,
    fps: float = Query(2.0, gt=0, le=10), size: int = Query(360, ge=128, le=720),
    max_frames: int = Query(60, ge=2, le=200), wait: float = Query(55.0, ge=0, le=120), band: str | None = BAND,
):
    """The whole time series as an animated GIF, embeddable anywhere."""
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, wait)
    if frames is None:
        return _pending(job)
    eps = _epochs(frames, _band(det, band), group, diff, grid=grid)
    if not eps:
        raise HTTPException(404, "no SPHEREx data at this position")
    limits = render.shared_limits(eps, diff)
    if len(eps) > max_frames:
        idx = sorted(set(np.linspace(0, len(eps) - 1, max_frames).round().astype(int)))
        eps = [eps[k] for k in idx]
    ims = [render.frame_image(e, limits, size=size, diff=diff, title=t.name) for e in eps]
    return Response(render.gif_bytes(ims, fps), media_type="image/gif", headers={"Cache-Control": "public, max-age=3600"})


def _point_flux(f, scale_arcsec: float) -> tuple[float, float, str, list[str]] | None:
    """Flux of the source at the centre of one frame: (flux, error, method,
    flags), or None when the frame does not measure it."""
    flux, err, flags = f.flux_ujy, f.flux_err_ujy, f.flags_at_target
    if np.isfinite(flux):
        return flux, err, "aperture", flags
    if not any(x in flags for x in wings.SATURATION_FLAGS):
        return None
    # The core has pixels without a value: the star saturates them, and only
    # its halo still measures it. A core that merely carries flags is summed
    # by the aperture (cutout.aperture_flux) and never comes here: the halo
    # is 1 % of the light, so whatever else lies around the source (a host
    # galaxy, a nebula, a neighbour) counts many times over in a halo flux.
    w = wings.measure(f.image, scale_arcsec, f.wave_um, f.bandwidth_um)
    if w is None:
        return None
    return w["flux_ujy"], w["flux_err_ujy"], "wings", flags + (["EXTRAPOLATED"] if w["extrapolated"] else [])


def _drop_stray_halo_points(pts: list[dict]) -> list[dict]:
    """Leave out the few halo points that stand among direct ones.

    A source the aperture measures still gets a halo point now and then: a
    cosmic-ray hit fills a pixel of its core in one exposure. Around
    anything but a single star that point reads high (the halo is 1 % of the
    light), and one such point in a curve of direct ones looks like a
    feature. So a halo point is dropped where, within two bandwidths, direct
    points outnumber halo points four to one. Where the two kinds are mixed
    (a star at the saturation limit, measured directly only when it falls
    between pixels) both stay: each fills gaps the other leaves.
    """
    wave = np.array([p["wave_um"] for p in pts])
    halo = np.array([p["method"] == "wings" for p in pts])
    if not halo.any() or halo.all():
        return pts
    keep = []
    for p, w, is_halo in zip(pts, wave, halo):
        if is_halo:
            near = np.abs(wave - w) <= 2.0 * (p["bandwidth_um"] or w / 41.0)
            direct = int(np.count_nonzero(near & ~halo))
            if direct >= 3 and direct >= 4 * int(np.count_nonzero(near & halo)):
                continue
        keep.append(p)
    return keep


@app.get("/api/spectrum")
def api_spectrum(
    target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
    wait: float = Query(0.0, ge=0, le=120), partial: bool = True,
):
    """On-the-fly spectrum and light curve of the central position.

    Each exposure sees the target through one narrow slice of a linear
    variable filter, so all exposures together sample 0.75-5.0 um. Points are
    aperture fluxes (r = 2 native pixels) with empirical sky-noise errors.
    Where the core has no usable value because the star saturates the
    detector, the point comes from its halo instead (``method`` = "wings",
    see ``sphereblink/wings.py``): good to about 20-25 % for a single star,
    and flagged EXTRAPOLATED beyond the calibrated range. A stray halo point
    among aperture points is left out.
    """
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, wait, partial=partial)
    if frames is None:
        return _pending(job)
    pts = []
    for f in frames:
        if not np.isfinite(f.wave_um):
            continue
        got = _point_flux(f, grid.scale_arcsec)
        if got is None:
            continue
        flux, err, method, flags = got
        pts.append({
            "wave_um": round(f.wave_um, 4),
            "bandwidth_um": round(f.bandwidth_um, 4) if np.isfinite(f.bandwidth_um) else None,
            "flux_ujy": round(flux, 2),
            "flux_err_ujy": round(err, 2) if np.isfinite(err) else None,
            "method": method,
            "detector": f.exposure.detector,
            "mjd": round(f.exposure.mjd, 5),
            "uid": f.exposure.uid,
            "flags": flags,
        })
    pts = _drop_stray_halo_points(pts)
    pts.sort(key=lambda p: p["wave_um"])
    return {"target": t.to_dict(), "job": job.public(), "units": {"flux": "uJy", "wave": "um"}, "points": pts}


@app.get("/api/catalog")
def api_catalog(
    cat: str = "gaia", target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
):
    """Brightest Gaia DR3 or CatWISE2020 sources in the field (with proper motions)."""
    t, grid = _view(target, ra, dec, fov)
    try:
        sources = _catalog(cat, grid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"catalogue service error: {type(exc).__name__}") from exc
    return {"cat": cat, "sources": sources}


@app.get("/api/object")
def api_object(target: str | None = None, ra: float | None = None, dec: float | None = None, second: bool = False):
    """What lies at a position and how far it is, from published catalogues.

    A star gets its distance from its parallax (Gaia DR3, or the parallax
    SIMBAD holds); a galaxy or a quasar gets its published redshift (SIMBAD,
    then NED) and what that redshift means in the Planck 2018 cosmology.
    Nothing here is measured from the SPHEREx images, and every value names
    its source. ``complete`` is false when a catalogue service did not
    answer: what there is comes back, and asking again later may add to it.
    ``second=1`` adds, where SIMBAD gave the redshift, what NED lists for the
    same object (``redshift.second``): the viewer asks for it when the lines
    of the SPHEREx spectrum disagree with the published value.
    """
    t = _target(target, ra, dec)
    named = t.source != "coordinates"
    key = f"object_v2_{t.name.lower() if named else ''}_{t.ra:.4f}_{t.dec:.4f}" + ("_second" if second else "")
    found = svc.store.get_catalog(key)
    if found is None:
        guard.charge(guard.catalogs, "catalogue queries")
        try:
            found = distance.lookup(t, second=second)
        except Exception as exc:
            raise HTTPException(502, f"catalogue service error: {type(exc).__name__}") from exc
        if found["complete"]:  # a partial answer is not kept: the next request tries the services again
            svc.store.put_catalog(key, found)
    return JSONResponse({"target": t.to_dict(), **found},
                        headers={"Cache-Control": "public, max-age=3600" if found["complete"] else "no-store"})


@app.get("/api/tracks")
def api_tracks(target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV):
    """Objects the team has measured in this field: catalogue position and
    proper motion, the fitted track, and the position in each exposure."""
    t, grid = _view(target, ra, dec, fov)
    if not measurements.enabled():
        return {"enabled": False, "sources": []}
    try:
        sources = measurements.near(grid.ra, grid.dec, grid.fov_arcmin / 60.0 * 0.75)
    except Exception as exc:
        raise HTTPException(502, f"measurement database error: {type(exc).__name__}") from exc
    return {"enabled": True, "sources": sources}


TILE_KEPT = "public, max-age=2592000, immutable"  # a tile of the atlas never changes under its address


@app.get("/api/atlas/2mass/Norder{order}/Dir{folder}/Npix{pix}.jpg")
def api_atlas_tile(order: int, folder: int, pix: int):
    """One tile of the photograph behind the sky map: the 2MASS colour atlas
    (J, H, K) in the HiPS layout, order 0-9, 512 pixel JPEGs exactly as the
    NASA/IPAC Infrared Science Archive serves them.

    A tile this server has is answered from its disk. Any other is fetched
    from the archive once and kept (``X-Atlas: kept`` or ``fetched``). When
    the archive does not answer, a tile that is not on disk gets ``503``.
    A backdrop for the map, not SPHEREx data. Credit: University of
    Massachusetts and IPAC/Caltech (NASA, NSF); HiPS by CDS.
    """
    if not (0 <= order <= atlas.MAX_ORDER and 0 <= pix < atlas.tiles_in(order) and folder == pix // 10000 * 10000):
        raise HTTPException(404, "no such tile")
    try:
        # only a tile that has to be fetched counts against the caller
        data, kept = sky.get(order, pix, charge=lambda: guard.charge(guard.atlas, "new sky tiles"))
    except atlas.NotThere as exc:
        raise HTTPException(404, str(exc)) from exc
    except atlas.Away as exc:
        raise HTTPException(503, f"sky atlas: {exc}", headers={"Retry-After": "30", "Cache-Control": "no-store"}) from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": TILE_KEPT, "X-Atlas": "kept" if kept else "fetched"})


@app.get("/api/atlas")
async def api_atlas():
    """What the server keeps of the sky photograph: tiles, bytes, the orders
    that are always kept and how far they are filled, and the credit."""
    return JSONResponse(sky.stats(), headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- web
if WEB.exists():
    class _Static(StaticFiles):
        """Static files with cache rules. The page asks for its scripts and
        styles as ``file.js?v=N``, so those never change under one address and
        browsers may keep them; fonts and pictures are kept for a day;
        anything else is checked with the server each time (a cheap 304)."""

        async def get_response(self, path: str, scope):
            r = await super().get_response(path, scope)
            if r.status_code in (200, 304):
                if b"v=" in scope.get("query_string", b""):
                    r.headers["Cache-Control"] = "public, max-age=604800, immutable"
                elif path.rsplit(".", 1)[-1].lower() in ("woff2", "woff", "ttf", "png", "svg", "jpg", "webp", "ico"):
                    r.headers["Cache-Control"] = "public, max-age=86400"
                else:
                    r.headers["Cache-Control"] = "no-cache"
            return r

    app.mount("/static", _Static(directory=WEB), name="static")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    async def robots():
        # pages may be indexed; the API is for the viewer (every call can start an archive read)
        return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /api/\n", headers={"Cache-Control": "public, max-age=86400"})

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    async def favicon():
        return FileResponse(WEB / "apple-touch-icon.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
