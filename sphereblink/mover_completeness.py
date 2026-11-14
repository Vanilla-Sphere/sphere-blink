"""How much of what moves does the blind search find: injection and recovery.

The search (``movers.py``) is measured the way surveys measure theirs. Fake
moving objects of known brightness, speed and direction are added to the
real exposures of a field, the unchanged search runs on them, and the share
it finds is its completeness for objects of that kind. The exposures keep
their real stars, noise, masked pixels and defects, so nothing about the
field is modelled; only the fake object is.

1. **Where.** A fake object moves on a straight line at constant speed on
   the sky (``Fake``). It is drawn at a random place of the field at the time
   of a random exposure, with a random direction (``draw``).
2. **How bright.** One number, the visual magnitude V, and a spectrum:
   sunlight reflected without colour (``sun``, what an asteroid is to first
   order) or the same flux density at every wavelength (``flat``). Every
   exposure has its own wavelength, so the object gets the flux of its
   spectrum there (``flux_mjy``).
3. **What it looks like.** The telescope puts most of a point source in one
   6.15 arcsec pixel, and the frames of a field are those pixels resampled
   on a common grid. The fake goes the same way (``stamp``): a point source
   through the telescope (a Gaussian, width per detector measured on the
   stars of the same frames), integrated over detector pixels that lie at a
   random offset and angle to the grid, then interpolated on the grid as the
   engine does. An object that moves during the 114 seconds of an exposure
   leaves a short trail.
4. **Into the frames, before anything else** (``inject``): the static sky of
   the search is the median of the frames, and a slow object partly ends up
   in it. That loss belongs to the result.
5. **Found or not** (``recover``): a fake is found when the search returns a
   path that passes within ``MATCH_ARCSEC`` of where the fake was, with its
   motion. Two levels are reported, because the search asks more of a path
   that no catalogue has an object on: ``found`` (the evidence asked of an
   object the catalogues know) and ``strong`` (the evidence asked of a
   nameless one).

The search is passed in as a function with the signature of
``movers.search`` (cube, frames, wcs, scale_arcsec), so any detector that
takes the same input is measured with the same fakes.

This module is pure computation: no network, no database.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import asdict, dataclass

import numpy as np
from scipy.special import erf

from . import movers

EXPOSURE_S = 113.6  # integration time of a SPHEREx exposure (XPOSURE in the Level 2 header)
NATIVE_ARCSEC = 6.15  # pixel of the detectors

# Width of a point source before the detector pixels, in detector pixels, per
# detector. Set so that the brightest pixel of a fake holds the share of the
# light that it holds for the stars of the test fields (median 0.31 in
# detectors 1 to 3, 0.25 in 4 to 6; see docs/mover-completeness.md).
PSF_SIGMA = {1: 0.40, 2: 0.40, 3: 0.40, 4: 0.52, 5: 0.52, 6: 0.52}
STAMP = 4  # pixels on each side of the object's place that a fake may reach

# Sunlight: AB magnitude at a wavelength minus the AB magnitude in V, from the
# Sun's absolute magnitudes in SDSS i and z, 2MASS J, H, Ks and WISE W1, W2
# (Willmer 2018, ApJS 236, 47, table 3). In between, straight in log wavelength.
SUN_COLOUR = ((0.55, 0.0), (0.75, -0.26), (0.90, -0.28), (1.24, -0.26), (1.65, -0.14), (2.16, 0.28), (3.37, 1.12),
              (4.62, 1.77), (5.20, 1.98))
AB_ZERO_MJY = 3631.0e3

MATCH_ARCSEC = 20.0  # a returned path this close to the fake's place, at the path's own reference time,
MATCH_RATE = 0.15  # with the motion within this share of the speed (or MATCH_RATE_MIN), is the fake
MATCH_RATE_MIN = 3.0  # arcsec per hour
NEAR_ARCSEC = 9.3  # a detection this close to the fake's place in its exposure is the fake's (1.5 pixels)
EDGE = 4  # pixels from the border of the field in which a fake is not counted as observable


@dataclass
class Fake:
    """One fake object: where it is at ``mjd0`` and how it moves from there."""

    ra0: float
    dec0: float
    mjd0: float
    v_east: float  # arcsec per hour on the sky
    v_north: float
    v_mag: float
    spectrum: str = "sun"
    label: int = 0

    @property
    def rate(self) -> float:
        return math.hypot(self.v_east, self.v_north)

    @property
    def pa_deg(self) -> float:
        return math.degrees(math.atan2(self.v_east, self.v_north)) % 360.0

    def at(self, mjd) -> tuple:
        dt = (np.asarray(mjd, dtype=float) - self.mjd0) * 24.0
        return movers.unproject(self.v_east * dt, self.v_north * dt, self.ra0, self.dec0)


# ----------------------------------------------------------------------------- brightness
def flux_mjy(v_mag: float, wave_um, spectrum: str = "sun") -> np.ndarray:
    """Flux density, in mJy, of an object of visual magnitude ``v_mag`` at a wavelength."""
    w = np.asarray(wave_um, dtype=float)
    colour = np.zeros_like(w)
    if spectrum == "sun":
        lw = np.log([p[0] for p in SUN_COLOUR])
        colour = np.interp(np.log(np.clip(w, SUN_COLOUR[0][0], SUN_COLOUR[-1][0])), lw, [p[1] for p in SUN_COLOUR])
    elif spectrum != "flat":
        raise ValueError("spectrum is 'sun' or 'flat'")
    return AB_ZERO_MJY * 10.0 ** (-0.4 * (v_mag + colour))


# ----------------------------------------------------------------------------- what a point source looks like in a frame
def _pixel_integral(m: np.ndarray, sigma: float) -> np.ndarray:
    """Share of a Gaussian centred on 0 that falls on the detector pixel centred on ``m``."""
    s = sigma * math.sqrt(2.0)
    return 0.5 * (erf((m + 0.5) / s) - erf((m - 0.5) / s))


def stamp(dx, dy, sigma: float, angle: float, phase: tuple[float, float], scale: float = 1.0,
          trail: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """A point source as the frames show it: share of its light per pixel of the grid.

    ``dx``, ``dy`` are the grid pixels' offsets from the source, in grid
    pixels (x to the right, y up). The source falls on detector pixels of
    width ``1 / scale`` grid pixels that are turned by ``angle`` and shifted
    by ``phase`` (in detector pixels) against the grid; the grid pixel gets
    the value interpolated between the four detector pixels around its
    centre, as ``cutout.cut_async`` does. ``trail`` is the path, in grid
    pixels, that the source covers during the exposure.
    """
    dx, dy = np.asarray(dx, dtype=float), np.asarray(dy, dtype=float)
    length = math.hypot(*trail)
    steps = max(1, int(math.ceil(length * 4.0)))  # a quarter of a pixel between the points of a trail
    out = np.zeros(np.broadcast(dx, dy).shape)
    c, s = math.cos(angle), math.sin(angle)
    for k in range(steps):
        f = (k + 0.5) / steps - 0.5
        x, y = (dx - f * trail[0]) * scale, (dy - f * trail[1]) * scale
        u, v = c * x + s * y + phase[0], -s * x + c * y + phase[1]  # in detector pixels; the source is at ``phase``
        u0, v0 = np.floor(u), np.floor(v)
        fu, fv = u - u0, v - v0
        gx0, gx1 = _pixel_integral(u0 - phase[0], sigma), _pixel_integral(u0 + 1 - phase[0], sigma)
        gy0, gy1 = _pixel_integral(v0 - phase[1], sigma), _pixel_integral(v0 + 1 - phase[1], sigma)
        out += ((1 - fu) * gx0 + fu * gx1) * ((1 - fv) * gy0 + fv * gy1)
    return out * (scale * scale / steps)


def _orientation(exposure: str, seed: int) -> tuple[float, tuple[float, float]]:
    """Angle and offset of the detector pixels against the grid for one exposure. The real ones are not kept
    with the frames; what matters is that they differ from exposure to exposure, as the real ones do."""
    rng = np.random.default_rng([seed, zlib.crc32(exposure.encode())])
    return float(rng.uniform(0, 2 * math.pi)), (float(rng.uniform(-0.5, 0.5)), float(rng.uniform(-0.5, 0.5)))


# ----------------------------------------------------------------------------- the noise of a field
def noise_maps(cube: np.ndarray, frames: list[dict], sky: movers.StaticSky | None = None, tile: int = movers.TILE) -> np.ndarray:
    """The noise the search itself sees, per frame and per cell of ``tile`` pixels:
    what one sigma of ``movers.snr_map`` is there. NaN where a frame has no usable pixel."""
    detectors = [int(f.get("detector") or 0) for f in frames]
    if sky is None:
        sky = movers.static_sky(cube, detectors)
    n, h, w = cube.shape
    ny, nx = -(-h // tile), -(-w // tile)
    out = np.full((n, ny, nx), np.nan, dtype=np.float32)
    for i in range(n):
        ref, mask = sky.of(detectors[i])
        maps = movers.snr_map(cube[i], ref, mask)
        if maps is None:
            continue
        s, res = maps
        with np.errstate(all="ignore"):
            sigma = np.where(s != 0, res / s, np.nan)
        pad = np.full((ny * tile, nx * tile), np.nan, dtype=np.float32)
        pad[:h, :w] = sigma
        cells = pad.reshape(ny, tile, nx, tile).transpose(0, 2, 1, 3).reshape(ny, nx, -1)
        ok = np.isfinite(cells).any(axis=2)
        out[i][ok] = np.nanmedian(cells[ok], axis=1)
    return out


# ----------------------------------------------------------------------------- fakes into the frames
def passes(frames: list[dict], gap_d: float = movers.PASS_GAP_D) -> list[np.ndarray]:
    """Exposure times of the field grouped as the search groups detections: by pass of the telescope."""
    t = np.unique(np.round([float(f["mjd"]) for f in frames], 6))
    if t.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(t) > gap_d) + 1
    return np.split(t, cuts)


def inject(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, fakes: list[Fake], seed: int = 0,
           noise: np.ndarray | None = None, tile: int = movers.TILE) -> list[list[dict]]:
    """Add the fakes to the frames, in place, and tell where each one went.

    A fake is added to the exposures of the pass that holds its ``mjd0`` (a
    real object crosses a field in one pass and is gone months later). For
    every fake the list of its sightings comes back: frame, time, detector,
    pixel position, flux, the value it added to its brightest pixel and,
    with ``noise`` (from ``noise_maps`` of the frames before the fakes), how
    many sigma of the search that is. ``observable`` is False where the
    place is at the border of the field or on pixels the exposure does not
    have.
    """
    n, h, w = cube.shape
    mjd = np.array([float(f["mjd"]) for f in frames])
    to_mjy = (scale_arcsec / movers.ARCSEC) ** 2 * 1e9  # MJy/sr in one pixel -> mJy
    ratio = scale_arcsec / NATIVE_ARCSEC
    groups = passes(frames)
    oy, ox = np.mgrid[-STAMP : STAMP + 1, -STAMP : STAMP + 1]
    told = []
    for fake in fakes:
        own = next((g for g in groups if g[0] - 1e-6 <= fake.mjd0 <= g[-1] + 1e-6), None)
        sightings: list[dict] = []
        told.append(sightings)
        if own is None:
            continue
        idx = np.flatnonzero((mjd >= own[0] - 1e-6) & (mjd <= own[-1] + 1e-6))
        ra, dec = fake.at(mjd[idx])
        xs, ys = wcs.world_to_pixel_values(ra, dec)
        # the trail of one exposure, in pixels of the grid (east is to the left)
        per_exposure = EXPOSURE_S / 3600.0 / scale_arcsec
        trail = (-fake.v_east * per_exposure, fake.v_north * per_exposure)
        for i, x, y, a, d in zip(idx, np.atleast_1d(xs), np.atleast_1d(ys), np.atleast_1d(ra), np.atleast_1d(dec)):
            cx, cy = int(round(float(x))), int(round(float(y)))
            if not (-STAMP <= cx < w + STAMP and -STAMP <= cy < h + STAMP):
                continue
            f = frames[i]
            detector = int(f.get("detector") or 0)
            wave = f.get("wave_um")
            wave = float(wave) if wave is not None and np.isfinite(wave) else None
            flux = float(flux_mjy(fake.v_mag, wave if wave is not None else 1.0, fake.spectrum))
            angle, phase = _orientation(str(f["exposure"]), seed)
            share = stamp(cx + ox - float(x), cy + oy - float(y), PSF_SIGMA.get(detector, 0.46), angle, phase, ratio, trail)
            add = (share * (flux / to_mjy)).astype(np.float32)
            y0, y1, x0, x1 = max(0, cy - STAMP), min(h, cy + STAMP + 1), max(0, cx - STAMP), min(w, cx + STAMP + 1)
            part = add[y0 - (cy - STAMP) : y1 - (cy - STAMP), x0 - (cx - STAMP) : x1 - (cx - STAMP)]
            inside = EDGE <= cx < w - EDGE and EDGE <= cy < h - EDGE
            core = cube[i, max(0, cy - 1) : cy + 2, max(0, cx - 1) : cx + 2] if inside else np.empty((0, 0))
            observable = bool(inside and np.isfinite(core).mean() > 0.6)
            cube[i, y0:y1, x0:x1] += part
            peak = float(part.max()) if part.size else 0.0
            one = {"frame": int(i), "exposure": str(f["exposure"]), "mjd": float(mjd[i]), "detector": detector, "wave_um": wave,
                   "x": round(float(x), 2), "y": round(float(y), 2), "ra": float(a), "dec": float(d), "flux_mjy": flux,
                   "peak": peak, "observable": observable}
            if noise is not None and inside:
                sigma = float(noise[i, min(cy // tile, noise.shape[1] - 1), min(cx // tile, noise.shape[2] - 1)])
                one["snr"] = round(peak / sigma, 2) if np.isfinite(sigma) and sigma > 0 else None
            sightings.append(one)
    return told


def draw(frames: list[dict], wcs, npix: int, count: int, rng, v_mag: tuple[float, float] = (14.0, 20.0),
         rate: tuple[float, float] = (0.5, 400.0), spectrum: str = "sun", first_label: int = 0) -> list[Fake]:
    """Fakes at random: a place of the field at the time of a random exposure, the direction uniform, the speed
    uniform in its logarithm between the two rates, the magnitude uniform between the two magnitudes."""
    times = np.unique(np.round([float(f["mjd"]) for f in frames], 6))
    out = []
    for k in range(count):
        x, y = rng.uniform(EDGE, npix - 1 - EDGE, 2)
        ra, dec = wcs.pixel_to_world_values(float(x), float(y))
        speed = math.exp(rng.uniform(math.log(rate[0]), math.log(rate[1])))
        pa = rng.uniform(0, 2 * math.pi)
        out.append(Fake(ra0=float(ra) % 360.0, dec0=float(dec), mjd0=float(rng.choice(times)), v_east=speed * math.sin(pa),
                        v_north=speed * math.cos(pa), v_mag=float(rng.uniform(*v_mag)), spectrum=spectrum,
                        label=first_label + k))
    return out


# ----------------------------------------------------------------------------- found or not
def _matches(track: movers.Track, fake: Fake) -> tuple[bool, float, float]:
    ra, dec = fake.at(track.mjd0)
    e, n = movers.project(float(ra), float(dec), track.ra0, track.dec0)
    sep = math.hypot(float(e), float(n))
    dv = math.hypot(track.v_east - fake.v_east, track.v_north - fake.v_north)
    return sep <= MATCH_ARCSEC and dv <= max(MATCH_RATE_MIN, MATCH_RATE * fake.rate), sep, dv


def same_path(a: movers.Track, b: movers.Track) -> bool:
    """Do two paths of two searches of one field describe one object."""
    return _matches(a, Fake(ra0=b.ra0, dec0=b.dec0, mjd0=b.mjd0, v_east=b.v_east, v_north=b.v_north, v_mag=0.0))[0]


def summary(fake: Fake, sightings: list[dict]) -> dict:
    """What the search could have seen of a fake: exposure times at which its place was observable, and its
    signal in the detector where it is brightest (median over that detector's exposures)."""
    seen = [s for s in sightings if s["observable"]]
    times = {round(s["mjd"], 4) for s in seen}
    out = {"times": len(times), "exposures": len(seen),
           "visits": movers._visits(np.array(sorted(times)) * 24.0) if times else 0,
           "hours": round((max(times) - min(times)) * 24.0, 2) if times else 0.0}
    per: dict[int, list[float]] = {}
    for s in seen:
        if s.get("snr") is not None:
            per.setdefault(s["detector"], []).append(s["snr"])
    if per:
        best = max(per, key=lambda d: float(np.median(per[d])))
        out.update(snr=round(float(np.median(per[best])), 2), snr_detector=best,
                   snr_all=round(float(np.median([v for d in per.values() for v in d])), 2),
                   times_5=len({round(s["mjd"], 4) for s in seen if (s.get("snr") or 0) >= movers.MIN_SNR}))
    return out


def recover(result: dict, fakes: list[Fake], told: list[list[dict]], baseline: list[movers.Track] | None = None) -> dict:
    """Compare what a search returned with the fakes that were put in.

    Per fake: was it found, on which evidence, how well its motion was
    measured, and how many of its exposures gave a detection of the search
    (which tells where a fake that was not found got lost). ``others``
    counts the returned paths that are neither a fake nor one of the
    ``baseline`` paths (what the same search finds without fakes): false
    alarms that the fakes caused.
    """
    tracks = list(result.get("tracks") or [])
    dets = list(result.get("detections") or [])
    by_frame: dict[str, list] = {}
    for d in dets:
        by_frame.setdefault(d.exposure, []).append(d)
    used = set()
    rows = []
    for fake, sightings in zip(fakes, told):
        row = {**asdict(fake), "rate": round(fake.rate, 3), "pa_deg": round(fake.pa_deg, 1), **summary(fake, sightings)}
        # detections of the search at the fake's place
        hit_times = set()
        for s in sightings:
            if not s["observable"]:
                continue
            for d in by_frame.get(s["exposure"], []):
                e, n = movers.project(d.ra, d.dec, s["ra"], s["dec"])
                if math.hypot(float(e), float(n)) <= NEAR_ARCSEC:
                    hit_times.add(round(s["mjd"], 4))
                    break
        row["detected_times"] = len(hit_times)
        best = None
        for k, t in enumerate(tracks):
            ok, sep, dv = _matches(t, fake)
            if ok:
                used.add(k)
                if best is None or t.n_times > best[0].n_times:
                    best = (t, sep, dv)
        row["found"] = best is not None
        if best is not None:
            t, sep, dv = best
            row.update(strong=bool(movers.strong(t.extra)) if "times_seen" in t.extra else None, sep_arcsec=round(sep, 2),
                       rate_error=round(t.rate - fake.rate, 3), dv=round(dv, 3), n_times=t.n_times,
                       times_seen=t.extra.get("times_seen"), times_covered=t.extra.get("times_covered"))
        else:
            row["strong"] = False
        rows.append(row)
    known = 0
    others = []
    for k, t in enumerate(tracks):
        if k in used:
            continue
        if baseline is not None and any(same_path(t, b) for b in baseline):
            known += 1
        else:
            others.append({"rate": round(t.rate, 2), "ra0": round(t.ra0, 5), "dec0": round(t.dec0, 5), "mjd0": round(t.mjd0, 5),
                           "n_times": t.n_times, "times_seen": t.extra.get("times_seen"),
                           "times_covered": t.extra.get("times_covered"),
                           "strong": bool(movers.strong(t.extra)) if "times_seen" in t.extra else None})
    return {"fakes": rows, "baseline_found": known, "others": others, "stats": result.get("stats", {})}


def trial(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, fakes: list[Fake], search=None, seed: int = 0,
          noise: np.ndarray | None = None, baseline: list[movers.Track] | None = None) -> dict:
    """One round: the fakes into a copy of the frames, the search, the comparison."""
    work = np.array(cube, dtype=np.float32, copy=True)
    told = inject(work, frames, wcs, scale_arcsec, fakes, seed=seed, noise=noise)
    result = (search or movers.search)(work, frames, wcs, scale_arcsec)
    return recover(result, fakes, told, baseline)


# ----------------------------------------------------------------------------- numbers
def wilson(found: int, total: int, z: float = 1.0) -> tuple[float, float]:
    """Range of the true share behind ``found`` of ``total`` (Wilson score; z = 1 is 68 %, 1.96 is 95 %)."""
    if total <= 0:
        return 0.0, 1.0
    p = found / total
    den = 1 + z * z / total
    mid = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def upper_limit(events: int, trials: int, confidence: float = 0.95) -> float:
    """Largest rate per trial that ``events`` in ``trials`` still allow (Poisson). None seen in 32 trials: 0.094."""
    from scipy.stats import chi2

    return float(chi2.ppf(confidence, 2 * (events + 1)) / 2.0 / trials) if trials else float("inf")


def table(rows: list[dict], key: str, edges, rate_edges, level: str = "found", min_times: int = movers.CANDIDATE_TIMES) -> list[dict]:
    """Share found per cell of brightness (``key``: ``v_mag`` or ``snr``) and speed, among the fakes that were
    observable at ``min_times`` exposure times or more (fewer, and no brightness could have saved them)."""
    out = []
    use = [r for r in rows if r.get("times", 0) >= min_times and r.get(key) is not None]
    for r0, r1 in zip(rate_edges[:-1], rate_edges[1:]):
        for a, b in zip(edges[:-1], edges[1:]):
            cell = [r for r in use if r0 <= r["rate"] < r1 and a <= r[key] < b]
            got = sum(bool(r[level]) for r in cell)
            lo, hi = wilson(got, len(cell))
            out.append({"rate": [r0, r1], key: [a, b], "n": len(cell), "found": got,
                        "share": round(got / len(cell), 4) if cell else None, "low": round(lo, 4), "high": round(hi, 4)})
    return out


def half_point(rows: list[dict], key: str = "v_mag", level: str = "found", share: float = 0.5,
               min_times: int = movers.CANDIDATE_TIMES) -> dict | None:
    """The brightness at which a given share of the fakes is found: a logistic curve in ``key`` fitted to the
    single fakes (found or not), with a plateau for what is lost at any brightness."""
    from scipy.optimize import minimize

    use = [r for r in rows if r.get("times", 0) >= min_times and r.get(key) is not None]
    if len(use) < 30:
        return None
    x = np.array([r[key] for r in use], dtype=float)
    y = np.array([bool(r[level]) for r in use], dtype=float)
    if y.sum() < 5 or (1 - y).sum() < 5:
        return None
    sign = -1.0 if np.corrcoef(x, y)[0, 1] < 0 else 1.0  # found less often as the magnitude grows, more as S/N grows

    def nll(p):
        top, mid, width = 1 / (1 + math.exp(-p[0])), p[1], math.exp(p[2])
        c = np.clip(top / (1 + np.exp(-sign * (x - mid) / width)), 1e-9, 1 - 1e-9)
        return -float(np.sum(y * np.log(c) + (1 - y) * np.log(1 - c)))

    best = None
    for mid0 in np.percentile(x, [30, 50, 70]):
        fit = minimize(nll, [3.0, mid0, math.log(0.3 * (np.std(x) + 1e-3))], method="Nelder-Mead",
                       options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 4000})
        if best is None or fit.fun < best.fun:
            best = fit
    top, mid, width = 1 / (1 + math.exp(-best.x[0])), float(best.x[1]), math.exp(best.x[2])
    if share >= top:
        return {"plateau": round(top, 4), "mid": round(mid, 3), "width": round(width, 3), "at": None, "n": len(use)}
    at = mid + sign * width * math.log(share / (top - share))
    return {"plateau": round(top, 4), "mid": round(mid, 3), "width": round(width, 3), "at": round(float(at), 3), "n": len(use)}
