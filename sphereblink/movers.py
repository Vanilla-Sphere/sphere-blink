"""Blind search for moving objects in the exposures of a field.

Nothing is known beforehand about what moves or where. The input is what the
viewer already has: every exposure of a field, resampled on one north-up
grid (``cutout.Frame.image``), each taken at another time and wavelength.

1. **Static sky.** The median of the frames of one detector, pixel by pixel,
   holds everything that stays in place: an object that moves is in a given
   pixel only for a few exposures and drops out of the median. One median per
   detector, because a star is not equally bright in every band. Pixels where
   the median is bright are stars and galaxies; they are masked.
2. **Detections.** In every frame minus its static sky, compact groups of
   pixels above the local noise. A single pixel is a detector defect or a
   cosmic-ray hit, not a source. What shows up at one place of the sky at
   times too far apart for anything that moves is dropped as well.
3. **Linking.** A moving object is a set of detections on a straight line at
   constant speed. Within one pass of the telescope over the field, every
   pair of detections proposes a motion; the proposal is voted on by all
   other detections of the pass and kept when detections of at least
   ``MIN_TIMES`` exposure times in ``MIN_VISITS`` visits lie on it. The best
   supported line is taken first and its detections are removed.
4. **Confirmation.** Along a track, every exposure that covers the predicted
   place is measured there. A real object is seen in most of them; a chance
   alignment is not.
5. **How sure.** The same search with the exposure times shuffled finds only
   chance alignments; their number is the false alarm figure of the field.

Detections carry sky coordinates, so those of fields opened earlier can be
linked together with new ones (`link` works on the sky, not on a pixel grid).

This module is pure computation: no network, no database.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import ndimage as ndi

VERSION = 2  # 2: a sighting must be a compact source in the pixels, not one bright pixel

MIN_SNR = 5.0  # peak of a detection, in units of the local noise
GROW_SNR = 2.5  # pixels joined to a peak
STATIC_SNR = 4.0  # median-image level, in its own noise, that marks a static source
STATIC_GROW = 1  # pixels the static mask is widened by
MIN_PIXELS = 2  # a real source covers more than one native pixel
MAX_PIXELS = 400  # larger groups are background structure, not an object
MIN_COVERAGE = 12  # frames a pixel must have for its median to be trusted
DETECTOR_FRAMES = 8  # frames of one detector a pixel needs for that detector's own median
MAX_PER_FRAME = 80  # a frame with more detections than this is a bad frame
TILE = 32  # pixels: size of the cells the local background and noise are taken in

PASS_GAP_D = 2.0  # exposures further apart than this belong to different passes
MIN_TIMES = 4  # exposure times a track must be seen at
MIN_VISITS = 2  # separate visits (orbits) among them: one burst of exposures is not a track
VISIT_GAP_H = 0.5  # exposures closer than this belong to one visit
MAX_GAP_H = 12.0  # longest time between the two detections that propose a motion
MIN_RATE = 2.0  # arcsec per hour; slower than this is a job for the slow (proper motion) list
MAX_RATE = 600.0  # arcsec per hour; faster is a long trail within one exposure
MIN_SHIFT = 18.0  # arcsec a track must cover in total (three native pixels)
PAIR_SEP = 9.0  # arcsec two detections must be apart to propose a motion
TOL = 7.5  # arcsec a detection may lie from the line (1.2 native pixels)
TOL_RATE = 0.15  # arcsec per hour the tolerance grows with: real paths are slightly curved
TOL_MAX = 15.0
RECUR = 6.2  # arcsec: detections this close are at the same place of the sky
MAX_PASS_DETECTIONS = 1500  # brightest kept per pass when a field has more
PROPOSALS = 6  # motions kept per starting detection
SEEN_SNR = 3.5  # level that counts as seen when a track is confirmed in the pixels
CORE_SNR = 3.0  # and the level of the brightest pixel together with its eight neighbours: light of a real object is
# spread over them by the telescope, a noise peak, a cosmic ray hit or a star's leftover is one pixel
CORE_SHARE = 1.3  # the nine together hold at least this many times the brightest alone (measured on asteroids: 1.4 to 3.4)
WINDOW = 24  # pixels around a predicted place that give its background and noise
MIN_SEEN_TIMES = 6  # exposure times a confirmed object is seen at (detected, or found at the predicted place)
MIN_SEEN = 0.7  # share of the times its path was covered at which it must be seen
MIN_SEEN_EXPOSURES = 0.5  # and of the single exposures that cover it
CANDIDATE_TIMES = 8  # a path that no catalogue has an object on needs more: seen at this many exposure times
CANDIDATE_SEEN = 0.8  # and at this share of the times it was covered (of 542 chance paths none came near)
MERGE_SEP = 25.0  # arcsec: two tracks this close with the same motion are one object
MERGE_RATE = 0.1  # relative difference in speed (or 3 arcsec per hour) that still is the same motion
FAST_RATE = 20.0  # arcsec per hour: speed classes of the result
VERY_FAST_RATE = 60.0

ARCSEC = 206264.806247


@dataclass
class Detection:
    exposure: str  # exposure id and detector: the same in every field that holds it
    mjd: float
    ra: float
    dec: float
    snr: float
    flux_mjy: float  # sum of the group
    npix: int
    detector: int = 0
    wave_um: float | None = None
    x: float | None = None  # pixel of the field it was found in
    y: float | None = None
    frame: int = -1  # index into the frames of that field

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, n in (("mjd", 6), ("ra", 6), ("dec", 6), ("snr", 2), ("flux_mjy", 4), ("wave_um", 4), ("x", 2), ("y", 2)):
            if d[k] is not None:
                d[k] = round(float(d[k]), n) if np.isfinite(d[k]) else None
        return d

    def key(self) -> tuple:
        """Same detection found again from an overlapping field."""
        return (self.exposure, round(self.ra * 1200.0), round(self.dec * 1200.0))


@dataclass
class Track:
    detections: list[Detection]
    mjd0: float  # reference time of the fit
    ra0: float  # position at mjd0
    dec0: float
    v_east: float  # arcsec per hour on the sky
    v_north: float
    rms_arcsec: float
    n_times: int
    n_visits: int
    extra: dict = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return math.hypot(self.v_east, self.v_north)

    @property
    def pa_deg(self) -> float:
        """Direction of motion, degrees east of north."""
        return math.degrees(math.atan2(self.v_east, self.v_north)) % 360.0

    @property
    def span_h(self) -> float:
        t = [d.mjd for d in self.detections]
        return (max(t) - min(t)) * 24.0

    def at(self, mjd) -> tuple:
        """Sky position at a time (linear motion in the tangent plane at ra0, dec0)."""
        dt = (np.asarray(mjd, dtype=float) - self.mjd0) * 24.0
        return unproject(self.v_east * dt, self.v_north * dt, self.ra0, self.dec0)

    def speed_class(self) -> str:
        return "very fast" if self.rate >= VERY_FAST_RATE else "fast" if self.rate >= FAST_RATE else "slow"

    def to_dict(self) -> dict:
        t = [d.mjd for d in self.detections]
        return {
            "mjd0": round(self.mjd0, 6), "ra0": round(self.ra0, 6), "dec0": round(self.dec0, 6),
            "v_east": round(self.v_east, 3), "v_north": round(self.v_north, 3),
            "rate_arcsec_hr": round(self.rate, 2), "pa_deg": round(self.pa_deg, 1),
            "rms_arcsec": round(self.rms_arcsec, 2), "n_detections": len(self.detections),
            "n_times": self.n_times, "n_visits": self.n_visits, "span_h": round(self.span_h, 2),
            "mjd_first": round(min(t), 6), "mjd_last": round(max(t), 6), "speed_class": self.speed_class(),
            "median_snr": round(float(np.median([d.snr for d in self.detections])), 1),
            "detections": [d.to_dict() for d in self.detections], **self.extra,
        }


# ----------------------------------------------------------------------------- sky geometry
def project(ra, dec, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """Tangent-plane offsets (east, north) in arcsec from (ra0, dec0)."""
    a, d = np.radians(np.asarray(ra, dtype=float)), np.radians(np.asarray(dec, dtype=float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    da = a - a0
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(da)
    east = np.cos(d) * np.sin(da) / cosc
    north = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(da)) / cosc
    return east * ARCSEC, north * ARCSEC


def unproject(east, north, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    x, y = np.asarray(east, dtype=float) / ARCSEC, np.asarray(north, dtype=float) / ARCSEC
    a0, d0 = math.radians(ra0), math.radians(dec0)
    den = math.cos(d0) - y * math.sin(d0)
    ra = a0 + np.arctan2(x, den)
    dec = np.arctan2(math.sin(d0) + y * math.cos(d0), np.hypot(x, den))
    return np.degrees(ra) % 360.0, np.degrees(dec)


# ----------------------------------------------------------------------------- static sky
def _median(cube: np.ndarray, rows: int = 48) -> tuple[np.ndarray, np.ndarray]:
    """Median over the frames and the number of frames behind each pixel, a
    band of rows at a time so that memory stays near the cube's."""
    n, h, w = cube.shape
    ref = np.full((h, w), np.nan, dtype=np.float32)
    cov = np.zeros((h, w), dtype=np.int32)
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        for r0 in range(0, h, rows):
            part = cube[:, r0 : r0 + rows]
            cov[r0 : r0 + rows] = np.isfinite(part).sum(axis=0)
            ref[r0 : r0 + rows] = np.nanmedian(part, axis=0)
    return ref, cov


def _mad(a: np.ndarray) -> float:
    v = a[np.isfinite(a)]
    if v.size < 20:
        return float("nan")
    return 1.4826 * float(np.median(np.abs(v - np.median(v))))


def _bright(ref: np.ndarray, snr: float, grow: int) -> np.ndarray:
    noise = _mad(ref)
    if not np.isfinite(noise) or noise <= 0:
        return np.ones(ref.shape, dtype=bool)
    b = np.nan_to_num(ref - float(np.nanmedian(ref)), nan=0.0) > snr * noise
    return ndi.binary_dilation(b, iterations=grow) if grow > 0 else b


@dataclass
class StaticSky:
    """What does not move in a field: one median image per detector and the
    pixels that belong to stars and galaxies."""

    ref: dict  # detector -> median image (0 = all detectors together)
    mask: dict  # detector -> pixels not searched

    def of(self, detector: int) -> tuple[np.ndarray, np.ndarray]:
        k = detector if detector in self.ref else 0
        return self.ref[k], self.mask[k]


def static_sky(cube: np.ndarray, detectors=None, snr: float = STATIC_SNR, grow: int = STATIC_GROW,
               min_coverage: int = MIN_COVERAGE) -> StaticSky:
    ref0, cov0 = _median(cube)
    mask0 = _bright(ref0, snr, grow) | (cov0 < min_coverage) | ~np.isfinite(ref0)
    ref, mask = {0: ref0}, {0: mask0}
    if detectors is not None:
        detectors = np.asarray(detectors)
        for d in sorted(set(int(v) for v in detectors)):
            sel = np.flatnonzero(detectors == d)
            if sel.size < DETECTOR_FRAMES:
                continue
            r, c = _median(cube[sel])
            own = c >= DETECTOR_FRAMES
            r = np.where(own, r, ref0)
            ref[d] = r
            mask[d] = mask0 | _bright(r, snr, grow)
    return StaticSky(ref=ref, mask=mask)


# ----------------------------------------------------------------------------- detections
def _local(res: np.ndarray, ok: np.ndarray, tile: int = TILE) -> tuple[np.ndarray, np.ndarray] | None:
    """Background and noise of a residual frame, cell by cell."""
    h, w = res.shape
    ny, nx = -(-h // tile), -(-w // tile)
    v = np.full((ny * tile, nx * tile), np.nan, dtype=np.float32)
    v[:h, :w] = np.where(ok, res, np.nan)
    v = v.reshape(ny, tile, nx, tile).transpose(0, 2, 1, 3).reshape(ny, nx, -1)[:, :, ::3]
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(v, axis=2)
        mad = 1.4826 * np.nanmedian(np.abs(v - med[:, :, None]), axis=2)
    thin = np.isfinite(v).mean(axis=2) < 0.25
    med[thin] = np.nan
    mad[thin | (mad <= 0)] = np.nan
    if not np.isfinite(mad).any():
        return None
    med = np.where(np.isfinite(med), med, np.nanmedian(med))
    mad = np.where(np.isfinite(mad), mad, np.nanmedian(mad))
    if ny > 1 and nx > 1:
        # cell values hold at cell centres; in between, straight interpolation
        yy = (np.arange(h) + 0.5) / tile - 0.5
        xx = (np.arange(w) + 0.5) / tile - 0.5
        coords = np.meshgrid(np.clip(yy, 0, ny - 1), np.clip(xx, 0, nx - 1), indexing="ij")
        bg = ndi.map_coordinates(med, coords, order=1, mode="nearest")
        noise = ndi.map_coordinates(mad, coords, order=1, mode="nearest")
    else:
        bg = np.full((h, w), float(med.flat[0]), dtype=np.float32)
        noise = np.full((h, w), float(mad.flat[0]), dtype=np.float32)
    return bg, noise


def snr_map(image: np.ndarray, ref: np.ndarray, static: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """A frame minus the static sky, in units of its local noise, and the
    residual itself. None when too little of the frame is usable."""
    res = image - ref
    ok = np.isfinite(res) & ~static
    if ok.sum() < 200:
        return None
    loc = _local(res, ok)
    if loc is None:
        return None
    bg, noise = loc
    res = res - bg
    return np.where(ok, res / noise, 0.0), np.where(ok, res, 0.0)


def detect_frame(image: np.ndarray, ref: np.ndarray, static: np.ndarray, min_snr: float = MIN_SNR,
                 grow_snr: float = GROW_SNR, min_pixels: int = MIN_PIXELS, max_pixels: int = MAX_PIXELS) -> list[tuple]:
    """Sources of one frame that the static sky does not explain:
    (x, y, peak S/N, summed residual, pixels) each."""
    maps = snr_map(image, ref, static)
    if maps is None:
        return []
    s, res = maps
    lab, n = ndi.label(s > grow_snr, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return []
    idx = np.arange(1, n + 1)
    peak = ndi.maximum(s, lab, idx)
    size = ndi.sum(np.ones(s.shape, dtype=np.float32), lab, idx)
    keep = idx[(peak >= min_snr) & (size >= min_pixels) & (size <= max_pixels)]
    if keep.size == 0:
        return []
    centres = ndi.center_of_mass(np.where(s > 0, s, 0.0) ** 2, lab, keep)  # towards the peak: comets have tails
    flux = np.atleast_1d(ndi.sum(res, lab, keep))
    return [(float(cx), float(cy), float(peak[k - 1]), float(f), int(size[k - 1]))
            for k, (cy, cx), f in zip(keep, centres, flux)]


def detect(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, sky: StaticSky | None = None,
           max_per_frame: int = MAX_PER_FRAME, **kw) -> tuple[list[Detection], StaticSky, dict]:
    """Detections of every frame of a field.

    ``frames`` gives for each image its ``exposure`` id, ``mjd``, ``detector``
    and ``wave_um``; ``wcs`` turns pixels of the field into sky coordinates.
    """
    detectors = [int(f.get("detector") or 0) for f in frames]
    if sky is None:
        sky = static_sky(cube, detectors)
    # a pixel of a finer view grid is not an independent native pixel
    native = max(1.0, (6.15 / scale_arcsec) ** 2)
    kw.setdefault("min_pixels", int(round(MIN_PIXELS * native)))
    kw.setdefault("max_pixels", int(round(MAX_PIXELS * native)))
    to_mjy = (scale_arcsec / ARCSEC) ** 2 * 1e9  # MJy/sr summed over pixels -> mJy
    dets: list[Detection] = []
    bad = 0
    for i, f in enumerate(frames):
        ref, mask = sky.of(detectors[i])
        found = detect_frame(cube[i], ref, mask, **kw)
        if len(found) > max_per_frame:
            bad += 1
            continue
        if not found:
            continue
        xs = np.array([v[0] for v in found])
        ys = np.array([v[1] for v in found])
        ra, dec = wcs.pixel_to_world_values(xs, ys)
        for (x, y, snr, flux, npix), a, d in zip(found, np.atleast_1d(ra), np.atleast_1d(dec)):
            w = f.get("wave_um")
            dets.append(Detection(exposure=str(f["exposure"]), mjd=float(f["mjd"]), ra=float(a), dec=float(d), snr=snr,
                                  flux_mjy=flux * to_mjy, npix=npix, detector=detectors[i],
                                  wave_um=float(w) if w is not None and np.isfinite(w) else None, x=x, y=y, frame=i))
    stats = {"frames": len(frames), "bad_frames": bad, "detections": len(dets),
             "masked_fraction": round(float(sky.mask[0].mean()), 3)}
    return dets, sky, stats


def drop_recurring(dets: list[Detection], radius: float = RECUR, min_rate: float = MIN_RATE) -> list[Detection]:
    """Without the detections that come back at the same place of the sky at
    times too far apart for anything that moves at ``min_rate`` or faster:
    residuals of stars, galaxies and bad pixels of the grid."""
    if len(dets) < 3:
        return list(dets)
    from scipy.spatial import cKDTree

    ra0 = float(np.median([d.ra for d in dets]))
    dec0 = float(np.median([d.dec for d in dets]))
    e, n = project([d.ra for d in dets], [d.dec for d in dets], ra0, dec0)
    t = np.array([d.mjd for d in dets]) * 24.0
    stay = 2.0 * radius / min_rate  # hours a mover can remain within the radius
    tree = cKDTree(np.column_stack([e, n]))
    keep = []
    for i, near in enumerate(tree.query_ball_point(np.column_stack([e, n]), radius)):
        others = np.unique(np.round(t[[j for j in near if abs(t[j] - t[i]) > stay]], 3))
        if others.size < 2:
            keep.append(dets[i])
    return keep


# ----------------------------------------------------------------------------- linking
def _visits(hours: np.ndarray, gap_h: float = VISIT_GAP_H) -> int:
    t = np.sort(np.unique(np.round(hours, 4)))
    return int(1 + np.sum(np.diff(t) > gap_h)) if t.size else 0


def _tolerance(dt, tol: float, tol_rate: float):
    return np.minimum(tol + tol_rate * np.abs(dt), max(TOL_MAX, tol))


def _line(t, e, n) -> tuple[float, float, float, float, float, float]:
    t0 = float(t.mean())
    a = np.column_stack([np.ones_like(t), t - t0])
    (e0, ve), *_ = np.linalg.lstsq(a, e, rcond=None)
    (n0, vn), *_ = np.linalg.lstsq(a, n, rcond=None)
    rms = float(np.sqrt(np.mean((e - e0 - ve * (t - t0)) ** 2 + (n - n0 - vn * (t - t0)) ** 2)))
    return t0, float(e0), float(n0), float(ve), float(vn), rms


def _link_pass(t, e, n, snr, min_times, min_visits, tol, tol_rate, min_rate, max_rate, max_gap_h, min_shift,
               pair_sep, proposals, max_tracks) -> list[tuple]:
    """Tracks among the detections of one pass. ``t`` in hours, ascending;
    ``e``, ``n`` in arcsec. Returns (members, t0, e0, n0, ve, vn, rms) each."""
    m = t.size
    if m < min_times:
        return []
    tid = np.concatenate([[0], np.cumsum(np.diff(t) > 1e-4)])  # index of the exposure time
    starts = np.flatnonzero(np.concatenate([[True], np.diff(tid) > 0]))
    if starts.size < min_times:
        return []
    cands = []
    for a in range(m):
        b = np.flatnonzero((t > t[a] + 1e-4) & (t <= t[a] + max_gap_h))
        if b.size == 0:
            continue
        dt = t[b] - t[a]
        de, dn = e[b] - e[a], n[b] - n[a]
        dist = np.hypot(de, dn)
        sel = (dist >= pair_sep) & (dist <= max_rate * dt) & (dist >= min_rate * dt)
        if not sel.any():
            continue
        b, dt = b[sel], dt[sel]
        ve, vn = de[sel] / dt, dn[sel] / dt
        # every proposal against every detection of the pass
        dta = t - t[a]
        lim = _tolerance(dta, tol, tol_rate)
        hit = ((e[a] + ve[:, None] * dta[None, :] - e[None, :]) ** 2
               + (n[a] + vn[:, None] * dta[None, :] - n[None, :]) ** 2) <= (lim * lim)[None, :]
        support = np.logical_or.reduceat(hit, starts, axis=1).sum(axis=1)
        good = np.flatnonzero(support >= min_times)
        if good.size == 0:
            continue
        for h in good[np.argsort(-support[good], kind="stable")[:proposals]]:
            cands.append((int(support[h]), a, float(ve[h]), float(vn[h])))
    cands.sort(key=lambda c: -c[0])
    alive = np.ones(m, dtype=bool)
    out = []
    for _, a, ve, vn in cands:
        if len(out) >= max_tracks:
            break
        if not alive[a]:
            continue
        t0, e0, n0 = t[a], e[a], n[a]
        members = np.zeros(0, dtype=int)
        for _ in range(3):  # collect, refit on what was collected, collect again
            dta = t - t0
            r = np.hypot(e0 + ve * dta - e, n0 + vn * dta - n)
            near = np.flatnonzero(alive & (r <= _tolerance(t - t[a], tol, tol_rate)))
            if near.size < min_times or np.unique(tid[near]).size < min_times:
                members = np.zeros(0, dtype=int)
                break
            members = near
            t0, e0, n0, ve, vn, rms = _line(t[members], e[members], n[members])
        if members.size == 0:
            continue
        rate = math.hypot(ve, vn)
        span = float(t[members].max() - t[members].min())
        if (np.unique(tid[members]).size < min_times or _visits(t[members]) < min_visits or rate * span < min_shift
                or not (min_rate <= rate <= max_rate) or rms > tol):
            continue
        alive[members] = False
        out.append((members, t0, e0, n0, ve, vn, rms))
    return out


def passes(dets: list[Detection], gap_d: float = PASS_GAP_D) -> list[list[Detection]]:
    """Detections grouped by pass of the telescope over the field."""
    if not dets:
        return []
    order = sorted(dets, key=lambda d: d.mjd)
    groups = [[order[0]]]
    for d in order[1:]:
        if d.mjd - groups[-1][-1].mjd > gap_d:
            groups.append([])
        groups[-1].append(d)
    return groups


def link(dets: list[Detection], min_times: int = MIN_TIMES, min_visits: int = MIN_VISITS, tol: float = TOL,
         tol_rate: float = TOL_RATE, min_rate: float = MIN_RATE, max_rate: float = MAX_RATE,
         max_gap_h: float = MAX_GAP_H, min_shift: float = MIN_SHIFT, pair_sep: float = PAIR_SEP,
         proposals: int = PROPOSALS, max_tracks: int = 30) -> list[Track]:
    """Tracks among detections: straight lines at constant speed on the sky,
    each within one pass."""
    tracks: list[Track] = []
    for group in passes(dets):
        if len(group) < min_times:
            continue
        if len(group) > MAX_PASS_DETECTIONS:
            group = sorted(sorted(group, key=lambda d: -d.snr)[:MAX_PASS_DETECTIONS], key=lambda d: d.mjd)
        ra0 = float(np.median([d.ra for d in group]))
        dec0 = float(np.median([d.dec for d in group]))
        e, n = project([d.ra for d in group], [d.dec for d in group], ra0, dec0)
        mjd = np.array([d.mjd for d in group])
        t = (mjd - mjd[0]) * 24.0
        snr = np.array([d.snr for d in group])
        for members, t0, e0, n0, ve, vn, rms in _link_pass(t, e, n, snr, min_times, min_visits, tol, tol_rate, min_rate,
                                                           max_rate, max_gap_h, min_shift, pair_sep, proposals,
                                                           max_tracks):
            chosen = [group[i] for i in members]
            ra, dec = unproject(e0, n0, ra0, dec0)
            tm = t[members]
            tracks.append(Track(detections=chosen, mjd0=float(mjd[0] + t0 / 24.0), ra0=float(ra), dec0=float(dec),
                                v_east=ve, v_north=vn, rms_arcsec=rms,
                                n_times=int(np.unique(np.round(tm, 4)).size), n_visits=_visits(tm)))
    tracks.sort(key=lambda k: (-k.n_times, k.rms_arcsec))
    return tracks


def false_alarms(dets: list[Detection], trials: int = 5, seed: int = 1, **kw) -> float:
    """Tracks per trial found when, within each pass, the exposure times are
    shuffled among the exposures: what chance alone links in this field."""
    if len(dets) < MIN_TIMES:
        return 0.0
    rng = np.random.default_rng(seed)
    found = 0
    for _ in range(trials):
        shuffled = []
        for group in passes(dets):
            times = sorted({round(d.mjd, 6) for d in group})
            swap = dict(zip(times, rng.permutation(times)))
            shuffled += [Detection(**{**asdict(d), "mjd": float(swap[round(d.mjd, 6)])}) for d in group]
        found += len(link(shuffled, **kw))
    return found / trials


# ----------------------------------------------------------------------------- one object, several pieces
def _track_of(dets: list[Detection]) -> Track:
    ra0 = float(np.median([d.ra for d in dets]))
    dec0 = float(np.median([d.dec for d in dets]))
    e, n = project([d.ra for d in dets], [d.dec for d in dets], ra0, dec0)
    mjd = np.array([d.mjd for d in dets])
    t = (mjd - mjd.min()) * 24.0
    t0, e0, n0, ve, vn, rms = _line(t, e, n)
    ra, dec = unproject(e0, n0, ra0, dec0)
    return Track(detections=sorted(dets, key=lambda d: d.mjd), mjd0=float(mjd.min() + t0 / 24.0), ra0=float(ra),
                 dec0=float(dec), v_east=ve, v_north=vn, rms_arcsec=rms, n_times=int(np.unique(np.round(t, 4)).size),
                 n_visits=_visits(t))


def same_object(a: Track, b: Track, sep: float = MERGE_SEP, rate: float = MERGE_RATE, max_gap_h: float = 24.0) -> bool:
    """Do two tracks describe one object: the same motion, and each one's
    line passes the other where the other was measured. A comet's coma and
    core, or the pieces of a path that is slightly curved, link separately."""
    if math.hypot(a.v_east - b.v_east, a.v_north - b.v_north) > max(3.0, rate * min(a.rate, b.rate)):
        return False
    ta = [d.mjd for d in a.detections]
    tb = [d.mjd for d in b.detections]
    if max(min(ta) - max(tb), min(tb) - max(ta), 0.0) * 24.0 > max_gap_h:
        return False
    for one, other in ((a, b), (b, a)):
        ra, dec = one.at(other.mjd0)
        e, n = project(float(ra), float(dec), other.ra0, other.dec0)
        if math.hypot(float(e), float(n)) > sep:
            return False
    return True


def merge(tracks: list[Track], **kw) -> list[Track]:
    """Tracks of one object joined into one."""
    out: list[Track] = []
    for t in sorted(tracks, key=lambda k: -len(k.detections)):
        for i, o in enumerate(out):
            if same_object(o, t, **kw):
                seen = {d.key() for d in o.detections}
                joined = _track_of(o.detections + [d for d in t.detections if d.key() not in seen])
                joined.extra = {**t.extra, **o.extra}
                out[i] = joined
                break
        else:
            out.append(t)
    return out


# ----------------------------------------------------------------------------- confirmation in the pixels
def confirm(track: Track, cube: np.ndarray, frames: list[dict], wcs, sky: StaticSky, scale_arcsec: float,
            seen_snr: float = SEEN_SNR, core_min: float = CORE_SNR, margin_h: float = 1.0) -> dict:
    """Measure the track's predicted place in every exposure of its time span.

    Gives how many exposures cover the place, in how many the object is seen,
    and the brightness in each: the object's spectrum, because every SPHEREx
    exposure is taken at another wavelength. Only detectors in which the
    track has a detection count: an object may be invisible in the others.

    Seen means two things at once: a pixel near the predicted place stands
    ``seen_snr`` above the noise, and that pixel with its eight neighbours,
    taken together, stands ``core_min`` above theirs and holds clearly more
    than the one pixel does. The second part is what tells an object from
    chance: in a crowded field single pixels above the
    first level are common (leftovers of stars, cosmic ray hits), and four of
    them on a line with a few more near the path used to pass as an object.
    """
    t = np.array([float(f["mjd"]) for f in frames])
    first = min(d.mjd for d in track.detections) - margin_h / 24.0
    last = max(d.mjd for d in track.detections) + margin_h / 24.0
    own = {d.detector for d in track.detections}
    h, w = cube.shape[1:]
    to_mjy = (scale_arcsec / ARCSEC) ** 2 * 1e9
    half = max(2, int(round(12.3 / scale_arcsec)))  # the box searched: the prediction is good to a pixel or two
    core = max(1, int(round(6.15 / scale_arcsec)))  # one native pixel around the brightest pixel of that box
    points = []
    for i in np.flatnonzero((t >= first) & (t <= last)):
        ra, dec = track.at(t[i])
        x, y = wcs.world_to_pixel_values(float(ra), float(dec))
        cx, cy = int(round(float(x))), int(round(float(y)))
        if not (half <= cx < w - half and half <= cy < h - half):
            continue
        detector = int(frames[i].get("detector") or 0)
        ref, mask = sky.of(detector)
        # the surroundings give the background and the noise; the whole frame is not needed
        y0, y1, x0, x1 = max(0, cy - WINDOW), min(h, cy + WINDOW + 1), max(0, cx - WINDOW), min(w, cx + WINDOW + 1)
        res = np.asarray(cube[i][y0:y1, x0:x1], dtype=np.float32) - ref[y0:y1, x0:x1]
        static = mask[y0:y1, x0:x1]
        by, bx = slice(cy - half - y0, cy + half + 1 - y0), slice(cx - half - x0, cx + half + 1 - x0)
        finite = np.isfinite(res[by, bx])
        if finite.mean() < 0.3:
            continue
        on_static = bool(static[cy - 1 - y0 : cy + 2 - y0, cx - 1 - x0 : cx + 2 - x0].mean() > 0.5)
        around = np.isfinite(res) & ~static
        around[by, bx] = False
        if around.sum() < 100:
            continue
        level = float(np.median(res[around]))
        noise = 1.4826 * float(np.median(np.abs(res[around] - level)))
        if not np.isfinite(noise) or noise <= 0:
            continue
        use = finite if on_static else finite & ~static[by, bx]
        if not use.any():
            continue
        inside = np.where(use, res[by, bx] - level, 0.0)
        peak, total = float(inside.max()) / noise, float(inside.sum())
        # the brightest pixel with its neighbours, as one measurement
        jy, jx = np.unravel_index(int(np.argmax(inside)), inside.shape)
        py, px = by.start + int(jy), bx.start + int(jx)
        cy_, cx_ = slice(max(0, py - core), py + core + 1), slice(max(0, px - core), px + core + 1)
        part = res[cy_, cx_] - level
        good = np.isfinite(part) if on_static else np.isfinite(part) & ~static[cy_, cx_]
        core_sum = float(np.where(good, part, 0.0).sum())
        core_snr = core_sum / (noise * math.sqrt(max(1, int(good.sum()))))
        compact = core_snr >= core_min and core_sum >= CORE_SHARE * float(inside.max())
        wave = frames[i].get("wave_um")
        points.append({"mjd": round(float(t[i]), 6), "exposure": str(frames[i]["exposure"]), "detector": detector,
                       "wave_um": round(float(wave), 4) if wave is not None and np.isfinite(wave) else None,
                       "ra": round(float(ra), 6), "dec": round(float(dec), 6), "snr": round(peak, 2),
                       "core_snr": round(core_snr, 2), "seen": bool(peak >= seen_snr and compact),
                       "flux_mjy": round(total * to_mjy, 4), "on_static": on_static,
                       "counted": (not on_static) and detector in own})
    return {**tally([d.mjd for d in track.detections], points), "points": points}


def tally(detected_mjd: list[float], points: list[dict]) -> dict:
    """How often the object is seen among the exposures that could show it.

    Evidence is counted in exposure times: the two detectors that look at the
    same sky at the same moment are one sighting, not two. A detection only
    counts where the pixels at the predicted place show a compact source too;
    it counts by itself only where that place could not be measured.
    """
    counted = [p for p in points if p["counted"]]
    seen = sum(bool(p["seen"]) for p in counted)
    own = {round(float(m), 4) for m in detected_mjd}
    measured = {round(p["mjd"], 4) for p in counted}
    times_seen = {round(p["mjd"], 4) for p in counted if p["seen"]} | (own - measured)
    return {"covered": len(counted), "seen": seen, "seen_fraction": round(seen / len(counted), 3) if counted else None,
            "times_seen": len(times_seen), "times_covered": len(own | measured)}


def confirmed(c: dict, min_times: int = MIN_SEEN_TIMES, min_seen: float = MIN_SEEN) -> bool:
    """Seen at enough different times, and at most of the times it could have
    been seen. Four detections on a line happen by chance among thousands;
    the same place lighting up at two more predicted times does not."""
    if c["times_seen"] < min_times or c["times_seen"] < min_seen * c["times_covered"]:
        return False
    return not c.get("covered") or c.get("seen", 0) >= MIN_SEEN_EXPOSURES * c["covered"]


def strong(c: dict) -> bool:
    """The evidence asked of a path without a name. Nearly everything bright
    enough for SPHEREx is a known asteroid or comet, so a nameless path is
    far more often chance than a new object, and has to show more."""
    return confirmed(c, min_times=CANDIDATE_TIMES, min_seen=CANDIDATE_SEEN)


# ----------------------------------------------------------------------------- the whole search of a field
def candidates(dets: list[Detection], cube, frames: list[dict], wcs, sky: StaticSky, scale_arcsec: float,
               **kw) -> tuple[list[Track], int]:
    """Tracks among the detections that the pixels confirm, and how many
    linked tracks the pixels rejected."""
    kept, rejected = [], 0
    for t in merge(link(dets, **kw)):
        c = confirm(t, cube, frames, wcs, sky, scale_arcsec)
        if confirmed(c):
            t.extra.update(covered=c["covered"], seen=c["seen"], seen_fraction=c["seen_fraction"], times_seen=c["times_seen"],
                           times_covered=c["times_covered"], points=c["points"])
            kept.append(t)
        else:
            rejected += 1
    kept.sort(key=lambda k: (-k.n_times, k.rms_arcsec))
    return kept, rejected


def chance_tracks(dets: list[Detection], cube, frames: list[dict], wcs, sky: StaticSky, scale_arcsec: float,
                  trials: int = 3, seed: int = 1, **kw) -> float:
    """The same search with the exposure times shuffled within each pass:
    the pictures stay, their order is lost, and with it every real motion.
    What is still found per trial is what chance produces in this field."""
    if len(dets) < MIN_TIMES:
        return 0.0
    rng = np.random.default_rng(seed)
    found = 0
    for _ in range(trials):
        swap: dict[float, float] = {}
        order = sorted({round(float(f["mjd"]), 6) for f in frames})
        groups = [[order[0]]] if order else []
        for m in order[1:]:
            if m - groups[-1][-1] > PASS_GAP_D:
                groups.append([])
            groups[-1].append(m)
        for g in groups:
            swap.update(zip(g, rng.permutation(g)))
        moved = [{**f, "mjd": float(swap[round(float(f["mjd"]), 6)])} for f in frames]
        shuffled = [Detection(**{**asdict(d), "mjd": float(swap.get(round(d.mjd, 6), d.mjd))}) for d in dets]
        found += len(candidates(shuffled, cube, moved, wcs, sky, scale_arcsec, **kw)[0])
    return found / trials


def search(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, known: list[Detection] | None = None,
           trials: int = 0) -> dict:
    """Everything for one field: detections, tracks the pixels confirm, and
    the numbers that say how far to trust them.

    ``known`` are detections of neighbouring fields searched earlier: they
    take part in the linking, so a track continues across the border.
    """
    dets, sky, stats = detect(cube, frames, wcs, scale_arcsec)
    fresh = drop_recurring(dets)
    stats["recurring"] = len(dets) - len(fresh)
    have = {d.key() for d in fresh}
    pool = fresh + [d for d in (known or []) if d.key() not in have]
    tracks, rejected = candidates(pool, cube, frames, wcs, sky, scale_arcsec)
    stats.update(linked=len(tracks) + rejected, rejected=rejected, tracks=len(tracks))
    if trials:
        stats["chance_tracks"] = chance_tracks(pool, cube, frames, wcs, sky, scale_arcsec, trials=trials)
    return {"detections": fresh, "tracks": tracks, "stats": stats, "version": VERSION}
