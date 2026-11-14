"""HTTP API + static web viewer.

Every view is addressed by URL parameters, so any image or movie can be
embedded elsewhere, e.g.
    /api/movie.gif?target=barnard&fov=8&det=all&group=day
    /?target=WISE%200855&fov=8
"""

from __future__ import annotations

import json
import struct
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import catalogs, config, measurements, render, tiles
from .cutout import Grid
from .resolve import FEATURED, ResolverUnavailable, Target, resolve
from .service import Service

WEB = Path(__file__).resolve().parent.parent / "web"
CUBE_BUDGET = 40_000_000  # max pixels (epochs * npix^2) sent to a browser at once

app = FastAPI(title="Vanilla Sphere", version="0.2.0", description=__doc__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"],
                   expose_headers=["X-Job"])
app.add_middleware(GZipMiddleware, minimum_size=2000, compresslevel=4)  # cubes are large: speed over the last few %
svc = Service()
fields = tiles.Fields(svc)


# --------------------------------------------------------------------------- helpers
@lru_cache(maxsize=512)
def _resolve_cached(text: str) -> Target:
    return resolve(text)


def _target(target: str | None, ra: float | None, dec: float | None) -> Target:
    try:
        if ra is not None and dec is not None:
            t = resolve(f"{ra} {dec}")
            if target:
                t.name = target
            return t
        if target:
            return _resolve_cached(target.strip())
        raise ValueError("give target=<name or coordinates> or ra=&dec=")
    except ResolverUnavailable as exc:  # the name services are down: not the caller's mistake
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    while (job.status in ("queued", "searching") or job.resuming) and time.time() - t0 < max(wait, 6.0):
        time.sleep(0.05)
    while job.status not in ("ready", "error") and time.time() - t0 < wait:
        time.sleep(0.2)
    if job.status == "error":
        raise HTTPException(502, f"data archive error: {job.error}")
    if job.status != "ready" and not partial:
        return None, job
    return job.snapshot(), job


_epoch_cache: "OrderedDict[tuple, list]" = OrderedDict()
_epoch_lock = threading.Lock()
EPOCH_CACHE_BYTES = 500e6


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
@app.get("/api/health")
def health():
    return {"ok": True, "collections": config.COLLECTIONS}


@app.get("/api/featured")
def featured():
    return [{"key": k, "name": v["name"], "note": v["note"]} for k, v in FEATURED.items()]


@app.get("/api/resolve")
def api_resolve(q: str):
    return _target(q, None, None).to_dict()


@app.get("/api/exposures")
def api_exposures(target: str | None = None, ra: float | None = None, dec: float | None = None):
    """Every SPHEREx Level 2 exposure covering the position (with S3 keys)."""
    t = _target(target, ra, dec)
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
    return Response(body, media_type="application/octet-stream", headers={"X-Job": job.status, "Cache-Control": "no-store"})


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


@app.get("/api/spectrum")
def api_spectrum(
    target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
    wait: float = Query(0.0, ge=0, le=120), partial: bool = True,
):
    """On-the-fly spectrum and light curve of the central position.

    Each exposure sees the target through one narrow slice of a linear
    variable filter, so all exposures together sample 0.75-5.0 um. Points are
    aperture fluxes (r = 2 native pixels) with empirical sky-noise errors.
    """
    t, grid = _view(target, ra, dec, fov)
    frames, job = _frames(grid, wait, partial=partial)
    if frames is None:
        return _pending(job)
    pts = []
    for f in frames:
        if not np.isfinite(f.flux_ujy) or not np.isfinite(f.wave_um):
            continue
        pts.append({
            "wave_um": round(f.wave_um, 4),
            "bandwidth_um": round(f.bandwidth_um, 4) if np.isfinite(f.bandwidth_um) else None,
            "flux_ujy": round(f.flux_ujy, 2),
            "flux_err_ujy": round(f.flux_err_ujy, 2) if np.isfinite(f.flux_err_ujy) else None,
            "detector": f.exposure.detector,
            "mjd": round(f.exposure.mjd, 5),
            "uid": f.exposure.uid,
            "flags": f.flags_at_target,
        })
    pts.sort(key=lambda p: p["wave_um"])
    return {"target": t.to_dict(), "job": job.public(), "units": {"flux": "uJy", "wave": "um"}, "points": pts}


@app.get("/api/catalog")
def api_catalog(
    cat: str = "gaia", target: str | None = None, ra: float | None = None, dec: float | None = None, fov: float = FOV,
):
    """Brightest Gaia DR3 or CatWISE2020 sources in the field (with proper motions)."""
    t, grid = _view(target, ra, dec, fov)
    key = f"{cat}_v2_{grid.key()}"
    sources = svc.store.get_catalog(key)
    if sources is None:
        try:
            sources = catalogs.query(cat, grid.ra, grid.dec, grid.fov_arcmin / 60.0 * 0.75)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"catalogue service error: {type(exc).__name__}") from exc
        svc.store.put_catalog(key, sources)
    return {"cat": cat, "sources": sources}


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


# --------------------------------------------------------------------------- web
if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})
