"""Shift-and-stack: moving objects too faint for one exposure, or too slow for the linker.

The blind search of ``movers.py`` needs an object to stand five times above
the noise in single exposures, and to move at least two arcsec per hour. A
body far beyond Neptune does neither: it is faint, and it creeps. This module
adds the method the searches for distant bodies use (digital tracking, Heinze
et al. 2015; KBMOD, Whidden et al. 2019 and Smotherman et al. 2021; the TESS
search of Rice & Laughlin 2020): for every motion an object could have, the
exposures are shifted so that such an object stays in place, and added. Noise
averages out, the object does not: a hundred exposures reach ten times deeper
than one.

1. **Frames.** Each frame minus the static sky, in units of its noise (the
   same maps the linker detects in). Values are limited to ``CLIP`` sigma: an
   object worth stacking is fainter than that in one frame, and a cosmic ray
   hit or the leftover of a star then cannot carry a stack by itself. Frames
   with too many outliers are left out.
2. **Matched filter.** Every frame is weighted by how bright the object is
   expected to be at the frame's wavelength (sunlight reflected by a cold
   body, or the same at all wavelengths) over the frame's noise, and smoothed
   with the shape of a point source. The frames of one visit (taken within
   minutes) are added without a shift. This gives, per visit, the two images
   of KBMOD: ``psi`` (weighted signal) and ``phi`` (weight); along a path the
   significance is ``sum(psi) / sqrt(sum(phi))``.
3. **Search.** Per pass of the telescope, every velocity on a grid is tried.
   The spacing of the grid follows Heinze et al. (2015, eq. 4): the largest
   blur a wrong velocity may cause over the time span, times sqrt(2), over
   the span. A first search on pixels of twice the size and a coarse grid
   finds seeds cheaply; each seed is searched again at full resolution on a
   fine grid. Slow motions use the whole pass, faster ones windows of a few
   days (the number of velocities stays the same, the depth goes down).
4. **Three parts.** A path counts only when each third of its time span,
   taken alone, shows the object too. A star's leftover, a cosmic ray hit or
   a fast object crossing the path lights up one part and fails.
5. **Vetting in the frames.** For each path left, the brightness at the
   predicted place is measured in every frame. Frames that disagree with the
   others are dropped (the sigma-G filter of KBMOD) and the significance is
   computed again; the same is done on paths parallel to it, a few pixels
   away, which gives the significance its local scale: in a crowded field the
   noise is neither Gaussian nor independent from pixel to pixel, and the
   neighbouring paths know. The stacked picture of the object must be a
   compact source at the predicted place.
6. **How sure.** The same search with the visits' times shuffled finds only
   chance (``chance_tracks``).

Results are ``movers.Track`` objects with ``extra["method"] == "stack"``.

This module is pure computation: no network, no database.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

from . import movers
from .movers import ARCSEC, Detection, StaticSky, Track

VERSION = 1

# ----------------------------------------------------------------------------- frames
CLIP = 2.5  # sigma a pixel of one frame may weigh in a stack
BAD_TAIL = 0.02  # share of pixels beyond 4 sigma that marks a frame as bad (typical: 0.003)
MIN_PIXELS = 2000  # usable pixels a frame needs
# A point source on the field's grid, centred on its brightest pixel (measured on asteroids of 10 to 150 sigma)
KERNEL = np.array([[0.10, 0.33, 0.10], [0.33, 1.00, 0.33], [0.10, 0.33, 0.10]], dtype=np.float32)
KERNEL /= KERNEL.sum()
KERNEL2 = KERNEL * KERNEL
_Q = np.zeros((4, 4), dtype=np.float32)  # the same source summed over 2 x 2 pixels
for _a in range(2):
    for _b in range(2):
        _Q[_a : _a + 3, _b : _b + 3] += KERNEL
_Q2 = _Q * _Q
T_SUN = 5772.0  # K
DETECTOR_UM = {1: 0.93, 2: 1.36, 3: 2.0, 4: 3.1, 5: 4.1, 6: 4.7}  # where a frame's own wavelength is missing

# ----------------------------------------------------------------------------- time
VISIT_GAP_MIN = 30.0  # frames closer than this are one visit: nothing searched for here moves a pixel in that time
PASS_GAP_D = 6.0  # visits further apart than this are searched apart (with the bend, a path holds for two weeks and more)
MIN_VISITS = 8  # visits a window needs
MIN_SPAN_H = 36.0  # and the time they must span

# ----------------------------------------------------------------------------- velocities
MIN_SHIFT = movers.MIN_SHIFT  # arcsec a path must cover (three native pixels): less is not motion
SLOW_RATE = 1.25  # arcsec per hour: up to here the whole pass is stacked
SLOW_FAR_RATE = 2.0  # and up to here for bodies beyond FAR_AU, whose motion is mostly Earth's own, seen from afar
FAR_GAMMA = 0.025  # 1/au: 40 au
MID_RATE = 4.0  # up to here windows of MID_WINDOW_H are stacked; faster objects belong to the linker
MID_WINDOW_H = 96.0
MID_STEP_H = 48.0
BLUR_COARSE = 2.0  # native pixels of blur the coarse grid of velocities allows (Heinze et al. 2015, b_max)
BLUR_FINE = 0.5
THIRDS = 3  # parts of the time span that each must show the object
THIRD_MIN_WEIGHT = 0.1  # a part with less than this share of the weight is not asked

# ----------------------------------------------------------------------------- thresholds
SEED_SNR = 4.3  # coarse search: what is looked at again
SEED_PART = 0.8  # and what each part must show there
FINE_SNR = 6.0  # full resolution, visit maps
FINE_PART = 1.2
MAX_SEEDS = {"slow": 400, "mid": 150}  # per window
MIN_SNR = 5.0  # in the frames, on the local scale: a path below this is not reported at all. Between this and
# STRONG_SNR chance still produces paths (about one per field): such a path counts only when a catalogue has an
# object there, with that motion (Eris, magnitude 18.7, is found at 5.6)
STRONG_SNR = 7.5  # what a path without a catalogue name must reach to be shown as a candidate (7 in Heinze et al. 2015)
PART_SNR = 1.5  # each third of the frames
MIN_TIMES = 12  # exposure times behind a stack
MIN_STACK_VISITS = 6
SIGMA_G = 2.5  # frames further than this many sigma-G from the others are dropped
MAX_DROPPED = 0.25  # share of the frames the filter may drop
STATIC_SHARE = 0.75  # a stack at rest, anywhere on the path, may reach this share of the moving one
SEEN_SNR = 2.0  # a frame in which the object shows by itself, weakly
WINDOW = 24  # pixels around a predicted place that give background and noise
CONTROL_RADII = (7, 11)  # pixels: parallel paths that give the local scale
# The stacked picture: a point source, not one pixel and not a smear
STAMP_OFF = 1.5  # pixels the brightest pixel may lie from the predicted place
STAMP_CORE_MIN = 1.3  # the nine central pixels hold at least this many times the brightest one
STAMP_RING_MAX = 0.2  # pixels three away are at most this bright, relative to the brightest (a halo: 0.3; a point source: below 0.1)
AVOID_PIXELS = 4.0  # frames in which a brighter moving object is this close are not used
MOAT_STAR = 40  # pixels: a masked patch of this size or more is a bright star (a faint one masks about nine).
MOAT_PIXELS = 3  # This close to it, what its light leaves behind at the edge of the mask changes with wavelength
# and from visit to visit: every strong false path of the first real fields lay there with nine tenths of its frames.
MOAT_CLEAR = 0.3  # So a path needs this share of its weight in frames away from bright stars,
MOAT_SIGNAL = 0.5  # and must show there at least this share of what it shows overall,
MOAT_ENOUGH = 3.0  # or this many sigma (Makemake's brightest frames happen to lie beside a star; the others carry it)
OFF_PIXELS = 3.0  # the object is "elsewhere" when it is this many pixels from a place
OFF_MIN = 4  # frames a place must have without the object
SIGHT_SNR = 5.0  # a single frame that shows the object by itself
VISIT_SNR = 1.5  # a visit that shows the object by itself
MIN_VISITS_SEEN = 3  # such visits a path needs
VISITS_POSITIVE = 0.7  # share of the visits in which the object must at least be above zero
PART_SHARE = 0.3  # each third shows at least this share of what an even source would

SAME_PIXELS = 4.0  # two paths this close at both ends of a window are one object
VET_CLIP = 3.0  # sigma one frame may weigh when a path is judged
MAX_SLOW_SPAN_H = 400.0  # a longer pass is searched in two halves that overlap
# Distance. Seen from the moving Earth an object at distance d is displaced by Earth's own path over d: within a
# pass that bends its track, and the bend gives the distance (Bernstein & Khushalani 2000). gamma = 1/d.
GAMMA_MAX = 0.06  # 1/au: nearer than 17 au a body moves too fast for this search anyway
RANGE_DROP = 9.0  # drop of the significance squared that bounds the range of distances given
ESCAPE = 208.8  # arcsec per hour at 1 au of the speed that leaves the Sun from there (42.1 km/s); falls as distance^-1.5
BOUND_SLACK = 1.3  # a path may need this many times that speed before it is called impossible
EPOCH_GAP_D = 30.0  # passes further apart than this are different epochs: a slow object is somewhere else by then


def template(wave_um, kind: str = "sun"):
    """Relative brightness (per unit frequency) expected at a wavelength.

    ``sun``: sunlight reflected by a neutral surface, the only light of a body
    beyond Jupiter at these wavelengths. ``flat``: the same at all
    wavelengths, a middle course for the asteroid belt, where reflected light
    falls and heat rises with wavelength.
    """
    w = np.asarray(wave_um, dtype=float)
    if kind == "flat":
        return np.ones_like(w)
    x = 14387.77 / (w * T_SUN)
    x1 = 14387.77 / (1.0 * T_SUN)
    return (1.0 / w**3) / np.expm1(x) * np.expm1(x1)


# ----------------------------------------------------------------------------- visits and passes
def visits_of(mjd: np.ndarray, gap_min: float = VISIT_GAP_MIN) -> list[np.ndarray]:
    """Frame indices grouped by visit, in order of time."""
    order = np.argsort(mjd, kind="stable")
    groups, start = [[int(order[0])]], float(mjd[order[0]])
    for i in order[1:]:
        if (mjd[i] - start) * 1440.0 > gap_min:
            groups.append([])
            start = float(mjd[i])
        groups[-1].append(int(i))
    return [np.array(g, dtype=int) for g in groups]


def _passes(times: np.ndarray, gap_d: float = PASS_GAP_D) -> list[np.ndarray]:
    """Indices of visits (sorted by time) grouped by pass."""
    cut = np.flatnonzero(np.diff(times) > gap_d) + 1
    return [p for p in np.split(np.arange(times.size), cut) if p.size]


def shuffled(frames: list[dict], seed: int = 1, visits: bool = True) -> list[dict]:
    """The frames with the times of the visits shuffled within each pass.

    The pictures stay and their order is lost, and with it every real motion;
    frames of one visit stay together. What a search still finds is chance.
    ``visits=False`` shuffles the single exposure times instead (the frames
    two detectors took at once stay together): visits are then lost too.
    """
    rng = np.random.default_rng(seed)
    mjd = np.array([float(f["mjd"]) for f in frames])
    groups = visits_of(mjd, VISIT_GAP_MIN if visits else 0.2)
    centre = np.array([mjd[g].mean() for g in groups])
    new = centre.copy()
    for p in _passes(centre):
        new[p] = centre[rng.permutation(p)]
    out = [dict(f) for f in frames]
    for g, old, t in zip(groups, centre, new):
        for i in g:
            out[i]["mjd"] = float(mjd[i] - old + t)
    return out


# ----------------------------------------------------------------------------- where the Earth is
def earth_offsets(mjd, ra0: float, dec0: float) -> tuple[np.ndarray, np.ndarray]:
    """What Earth's motion does to the place of an object at 1 au, seen in the
    direction (ra0, dec0): offsets to the east and north in au (radians per
    1/au), at each time. For an object at d au, divide by d."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    pos = get_body_barycentric("earth", Time(np.atleast_1d(mjd), format="mjd", scale="utc"), ephemeris="builtin").xyz.to_value("au")
    a, d = math.radians(ra0), math.radians(dec0)
    east = np.array([-math.sin(a), math.cos(a), 0.0])
    north = np.array([-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d)])
    return -(east @ pos), -(north @ pos)


def bend(mjd: np.ndarray, ra0: float, dec0: float, scale_arcsec: float) -> np.ndarray:
    """The part of Earth's effect that a straight line cannot follow, at each
    time: pixels (x, y) per unit of gamma = 1/distance[au]. Shape (n, 2)."""
    try:
        e, n = earth_offsets(mjd, ra0, dec0)
    except Exception:  # no ephemeris: straight lines only
        return np.zeros((len(mjd), 2))
    t = (np.asarray(mjd, dtype=float) - float(np.mean(mjd))) * 24.0
    if t.size >= 3 and np.ptp(t) > 0:
        e = e - np.polyval(np.polyfit(t, e, 1), t)
        n = n - np.polyval(np.polyfit(t, n, 1), t)
    else:
        e, n = e * 0.0, n * 0.0
    return np.column_stack([-e, n]) * (ARCSEC / scale_arcsec)


def reflex(mjd: np.ndarray, ra0: float, dec0: float, scale_arcsec: float) -> np.ndarray:
    """The straight part of Earth's effect: the motion a body at rest would
    show from the moving Earth, pixels per hour (x, y) per unit of gamma.
    Together with ``bend`` this is all of Earth's effect on the path."""
    try:
        e, n = earth_offsets(mjd, ra0, dec0)
    except Exception:
        return np.zeros(2)
    t = (np.asarray(mjd, dtype=float) - float(np.mean(mjd))) * 24.0
    if t.size < 3 or np.ptp(t) <= 0:
        return np.zeros(2)
    return np.array([-np.polyfit(t, e, 1)[0], np.polyfit(t, n, 1)[0]]) * (ARCSEC / scale_arcsec)


def bound_velocities(vels: np.ndarray, g_lo: float, g_hi: float, earth: np.ndarray, margin: float) -> np.ndarray:
    """Which of the velocities (arcsec per hour, (n, 2)) a body on a closed
    orbit around the Sun can show at a distance between 1/g_hi and 1/g_lo:
    Earth's part, ``gamma * earth``, plus a motion of its own that is below
    the speed of escape at that distance (Bernstein & Khushalani 2000)."""
    a, b = g_lo * earth, g_hi * earth
    ab = b - a
    den = float(ab @ ab)
    u = np.clip(((vels - a) @ ab) / den, 0.0, 1.0) if den > 0 else np.zeros(len(vels))
    near = a[None, :] + u[:, None] * ab[None, :]
    gam = g_lo + u * (g_hi - g_lo)
    # the own motion may be larger where the body is nearer: test against the nearest distance of the range too
    d_near = np.hypot(vels[:, 0] - near[:, 0], vels[:, 1] - near[:, 1])
    d_hi = np.hypot(vels[:, 0] - b[0], vels[:, 1] - b[1])
    return (d_near <= ESCAPE * gam**1.5 + margin) | (d_hi <= ESCAPE * g_hi**1.5 + margin)


# ----------------------------------------------------------------------------- the static sky of a slow object
def _median_of(cube, sel: np.ndarray, rows: int = 48) -> tuple[np.ndarray, np.ndarray]:
    import warnings

    _, h, w = cube.shape
    ref = np.full((h, w), np.nan, dtype=np.float32)
    cov = np.zeros((h, w), dtype=np.int32)
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        for r0 in range(0, h, rows):
            part = np.asarray(cube[sel, r0 : r0 + rows])
            cov[r0 : r0 + rows] = np.isfinite(part).sum(axis=0)
            ref[r0 : r0 + rows] = np.nanmedian(part, axis=0)
    return ref, cov


def _bright(ref: np.ndarray, snr: float = movers.STATIC_SNR, grow: int = movers.STATIC_GROW) -> np.ndarray:
    """Pixels of a median image that hold a star or a galaxy."""
    v = ref[np.isfinite(ref)]
    if v.size < 20:
        return np.ones(ref.shape, dtype=bool)
    centre = float(np.median(v))
    noise = 1.4826 * float(np.median(np.abs(v - centre)))
    if not np.isfinite(noise) or noise <= 0:
        return np.ones(ref.shape, dtype=bool)
    b = np.nan_to_num(ref - centre, nan=0.0) > snr * noise
    return ndi.binary_dilation(b, iterations=grow) if grow > 0 else b


def moat_of(sky: StaticSky) -> np.ndarray:
    """The surroundings of bright stars: pixels within MOAT_PIXELS of a large masked patch."""
    base = sky.mask[0]
    labels, n = ndi.label(base)
    if not n:
        return np.zeros(base.shape, dtype=bool)
    sizes = ndi.sum(base, labels, np.arange(1, n + 1))
    big = np.isin(labels, 1 + np.flatnonzero(sizes >= MOAT_STAR))
    return ndi.binary_dilation(big, iterations=MOAT_PIXELS) if big.any() else big


def epochs_of(mjd: np.ndarray, gap_d: float = EPOCH_GAP_D) -> list[np.ndarray]:
    order = np.argsort(mjd, kind="stable")
    cut = np.flatnonzero(np.diff(mjd[order]) > gap_d) + 1
    return [np.sort(g) for g in np.split(order, cut)]


def sky_without(cube, detectors: np.ndarray, own: np.ndarray, whole: StaticSky) -> StaticSky:
    """The static sky for the frames ``own`` of one epoch, made from the
    frames of the other epochs only.

    The median of all frames is fine for an object that crosses a pixel in a
    few exposures. One that stays within a pixel for days is in a fifth or
    more of the frames a detector ever took of that pixel: it shows in the
    median, faintly, and because the median of hundreds of frames is far
    deeper than one frame, that is enough to mark it a star and mask it
    (Eris, seen at 3 to 4 sigma per exposure, stands 6 sigma in the median).
    Half a year earlier or later the object is elsewhere, so the frames of
    the other epochs hold the stars and not the object. Where they do not
    cover a pixel well enough, the median of all frames is used.
    """
    others = np.setdiff1d(np.arange(cube.shape[0]), own)
    if others.size < 3 * movers.MIN_COVERAGE:
        return whole
    ref0, cov0 = _median_of(cube, others)
    thin = (cov0 < movers.MIN_COVERAGE) | ~np.isfinite(ref0)
    ref0 = np.where(thin, whole.ref[0], ref0)
    mask0 = np.where(thin, whole.mask[0], _bright(np.where(thin, np.nan, ref0))) | ~np.isfinite(ref0)
    ref, mask = {0: ref0}, {0: mask0}
    for d in sorted(set(int(v) for v in detectors[own])):
        sel = others[detectors[others] == d]
        if sel.size < movers.DETECTOR_FRAMES:
            continue
        r, c = _median_of(cube, sel)
        r = np.where(c >= movers.DETECTOR_FRAMES, r, ref0)
        ref[d] = r
        mask[d] = mask0 | (_bright(r) & ~thin)
    return StaticSky(ref=ref, mask=mask)


# ----------------------------------------------------------------------------- psi and phi
def _frame_snr(image, ref, mask):
    """A frame in units of its noise, limited to CLIP, and the noise (one number). None for a bad frame."""
    maps = movers.snr_map(image, ref, mask)
    if maps is None:
        return None
    s, res = maps
    used = s != 0
    n = int(used.sum())
    if n < MIN_PIXELS:
        return None
    v = s[used]
    if float((np.abs(v) > 4.0).mean()) > BAD_TAIL:
        return None
    big = np.abs(v) > 0.5
    if big.sum() < 100:
        return None
    noise = float(np.median(np.abs(res[used][big] / v[big])))
    if not np.isfinite(noise) or noise <= 0:
        return None
    np.clip(s, -CLIP, CLIP, out=s)
    return s.astype(np.float32, copy=False), used, noise


def _coarse(psi: np.ndarray, weight: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """psi and phi on pixels of twice the size: a source's light summed over 2 x 2."""
    h, w = psi.shape[0] // 2, psi.shape[1] // 2
    cpsi = psi[0 : 2 * h : 2, 0 : 2 * w : 2] + psi[1 : 2 * h : 2, 0 : 2 * w : 2] + psi[0 : 2 * h : 2, 1 : 2 * w : 2] + psi[1 : 2 * h : 2, 1 : 2 * w : 2]
    wp = np.pad(weight, ((1, 2), (1, 2)))
    cphi = np.zeros((h, w), dtype=np.float32)
    for a in range(4):
        for b in range(4):
            cphi += _Q2[a, b] * wp[a : a + 2 * h : 2, b : b + 2 * w : 2]
    return cpsi, cphi


@dataclass
class PassMaps:
    """The visits of one pass, ready to be stacked."""

    hours: np.ndarray  # time of each visit, hours from the first
    mjd0: float  # the first visit
    psi: np.ndarray  # (visits, h, w)
    phi: np.ndarray
    cpsi: np.ndarray  # the same on pixels of twice the size
    cphi: np.ndarray
    members: list  # frame indices of each visit (the frames that were used)
    dropped: int = 0  # frames left out


def pass_maps(cube, frames: list[dict], sky: StaticSky, groups: list[np.ndarray], kind: str, noise_ref: float) -> PassMaps | None:
    h, w = cube.shape[1:]
    psi, phi, cpsi, cphi, hours, members = [], [], [], [], [], []
    dropped = 0
    mjd_first = None
    for g in groups:
        a = np.zeros((h, w), dtype=np.float32)
        wsum = np.zeros((h, w), dtype=np.float32)
        used = []
        for i in g:
            detector = int(frames[i].get("detector") or 0)
            ref, mask = sky.of(detector)
            out = _frame_snr(cube[i], ref, mask)
            if out is None:
                dropped += 1
                continue
            s, ok, noise = out
            f = float(template(frames[i].get("wave_um") or DETECTOR_UM.get(detector, 2.0), kind))
            k = f * noise_ref / noise
            a += k * s
            wsum += (k * k) * ok
            used.append(int(i))
        if not used:
            continue
        p = ndi.correlate(a, KERNEL, mode="constant")
        q = ndi.correlate(wsum, KERNEL2, mode="constant")
        cp, cq = _coarse(p, wsum)
        t = float(np.mean([float(frames[i]["mjd"]) for i in used]))
        if mjd_first is None:
            mjd_first = t
        psi.append(p), phi.append(q), cpsi.append(cp), cphi.append(cq), members.append(np.array(used)), hours.append((t - mjd_first) * 24.0)
    if len(psi) < MIN_VISITS:
        return None
    order = np.argsort(hours)
    return PassMaps(hours=np.array(hours)[order], mjd0=mjd_first, psi=np.stack(psi)[order], phi=np.stack(phi)[order],
                    cpsi=np.stack(cpsi)[order], cphi=np.stack(cphi)[order], members=[members[i] for i in order], dropped=dropped)


# ----------------------------------------------------------------------------- the search of a window
def velocity_grid(v_min: float, v_max: float, step: float) -> np.ndarray:
    """Velocities (x, y) on a square grid, v_min <= speed <= v_max. Any units."""
    n = int(math.floor(v_max / step))
    g = np.arange(-n, n + 1) * step
    vx, vy = np.meshgrid(g, g)
    r = np.hypot(vx, vy)
    sel = (r <= v_max + 1e-9) & (r >= v_min - 1e-9)
    return np.column_stack([vx[sel], vy[sel]])


def _parts(hours: np.ndarray, n: int = THIRDS) -> np.ndarray:
    """Which part of the time span each visit belongs to: equal numbers of visits."""
    return np.minimum((np.arange(hours.size) * n) // max(1, hours.size), n - 1)


def _slices(d: int, n: int) -> tuple[slice, slice]:
    """Destination and source ranges for ``dst[i] += src[i + d]``."""
    return slice(max(0, -d), n + min(0, -d)), slice(max(0, d), n + min(0, d))


def _score(P: np.ndarray, F: np.ndarray, part_min: float, kappa: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Significance of the whole stack, and the same set to zero where a part
    of the time span that carries weight does not show the object."""
    ft = F.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        total = np.where(ft > 0, P.sum(axis=0) / np.sqrt(ft), 0.0) / kappa
        ok = np.ones(total.shape, dtype=bool)
        asked = np.zeros(total.shape, dtype=np.int8)
        for g in range(P.shape[0]):
            has = F[g] > THIRD_MIN_WEIGHT * ft
            part = np.where(has, P[g] / np.sqrt(np.where(has, F[g], 1.0)), 0.0) / kappa
            ok &= ~has | (part >= part_min)
            asked += has
    return total, np.where(ok & (asked >= 2), total, 0.0)


def stack_search(psi: np.ndarray, phi: np.ndarray, tau: np.ndarray, vels: np.ndarray, part_min: float,
                 kappa: float | None = None, curve: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    """The best stack of every pixel over a list of motions.

    ``psi``, ``phi``: (visits, h, w). ``tau``: hours of each visit from the
    reference time. ``vels``: (n, 2) pixels per hour (x, y), or (n, 3) with
    gamma = 1/distance, which bends the path by ``gamma * curve`` (``curve``:
    (visits, 2) pixels). A pixel of the result is where the straight part of
    the path is at the reference time. Returns the best
    significance per pixel, the index of its velocity, and the factor by
    which the plain significance was too large (noise is neither Gaussian nor
    independent between pixels; measured on the stacks themselves).
    """
    k, h, w = psi.shape
    parts = _parts(tau)
    n_parts = int(parts.max()) + 1
    P = np.zeros((n_parts, h, w), dtype=np.float32)
    F = np.zeros((n_parts, h, w), dtype=np.float32)

    def stack(vx: float, vy: float, gamma: float = 0.0) -> None:
        P[:] = 0
        F[:] = 0
        if gamma and curve is not None:
            dx = np.rint(vx * tau + gamma * curve[:, 0]).astype(int)
            dy = np.rint(vy * tau + gamma * curve[:, 1]).astype(int)
        else:
            dx = np.rint(vx * tau).astype(int)
            dy = np.rint(vy * tau).astype(int)
        for i in range(k):
            if abs(dy[i]) >= h or abs(dx[i]) >= w:
                continue
            yd, ys = _slices(int(dy[i]), h)
            xd, xs = _slices(int(dx[i]), w)
            g = parts[i]
            P[g, yd, xd] += psi[i, ys, xs]
            F[g, yd, xd] += phi[i, ys, xs]

    if kappa is None:
        widths = []
        for j in np.linspace(0, len(vels) - 1, min(len(vels), 7)).astype(int):
            stack(*vels[j])
            total, _ = _score(P, F, -np.inf)
            v = total[F.sum(axis=0) > 0]
            if v.size > 500:
                widths.append(1.4826 * float(np.median(np.abs(v - np.median(v)))))
        kappa = float(np.median(widths)) if widths else 1.0
        if not np.isfinite(kappa) or kappa <= 0:
            kappa = 1.0
    best = np.zeros((h, w), dtype=np.float32)
    arg = np.full((h, w), -1, dtype=np.int32)
    for j, v in enumerate(vels):
        stack(*v)
        _, score = _score(P, F, part_min, kappa)
        better = score > best
        best[better] = score[better]
        arg[better] = j
    return best, arg, kappa


@dataclass
class Candidate:
    """A path found in the visit maps. Pixels of the field, hours from ``mjd_ref``."""

    x: float
    y: float
    vx: float  # pixels per hour
    vy: float
    snr: float
    mjd_ref: float
    first_mjd: float
    last_mjd: float
    tier: str
    gamma: float = 0.0  # 1 / distance in au
    curve_mjd: np.ndarray | None = None  # the bend of the path per unit of gamma, at these times: pixels (x, y)
    curve: np.ndarray | None = None
    extra: dict = field(default_factory=dict)

    def at(self, mjd, gamma: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        t = np.asarray(mjd, dtype=float)
        dt = (t - self.mjd_ref) * 24.0
        x, y = self.x + self.vx * dt, self.y + self.vy * dt
        g = self.gamma if gamma is None else gamma
        if g and self.curve is not None:
            x = x + g * np.interp(t, self.curve_mjd, self.curve[:, 0])
            y = y + g * np.interp(t, self.curve_mjd, self.curve[:, 1])
        return x, y


def _refine(maps: PassMaps, sel: np.ndarray, t_ref: float, x: int, y: int, vx: float, vy: float, step: float, reach: int,
            kappa: float, gammas: np.ndarray, curve: np.ndarray, half: int = 3) -> tuple[float, float, float, float, float, float] | None:
    """The best path at full resolution near a seed: positions within ``half``
    pixels of the 2 x 2 block (x, y), velocities within ``reach`` fine steps,
    each of the distances ``gammas``."""
    psi, phi = maps.psi, maps.phi
    _, h, w = psi.shape
    tau = maps.hours[sel] - t_ref
    parts = _parts(tau)
    g = np.arange(-reach, reach + 1) * step
    vxs, vys, gs = np.meshgrid(vx + g, vy + g, gammas, indexing="ij")
    vxs, vys, gs = vxs.ravel(), vys.ravel(), gs.ravel()
    dx = np.rint(vxs[:, None] * tau[None, :] + gs[:, None] * curve[None, :, 0]).astype(int)  # (motions, visits)
    dy = np.rint(vys[:, None] * tau[None, :] + gs[:, None] * curve[None, :, 1]).astype(int)
    py = np.arange(y - half, y + half + 2)
    px = np.arange(x - half, x + half + 2)
    iy = py[None, None, :, None] + dy[:, :, None, None]
    ix = px[None, None, None, :] + dx[:, :, None, None]
    inside = (iy >= 0) & (iy < h) & (ix >= 0) & (ix < w)
    iy, ix = np.clip(iy, 0, h - 1), np.clip(ix, 0, w - 1)
    kk = sel[None, :, None, None]
    p = np.where(inside, psi[kk, iy, ix], 0.0)
    f = np.where(inside, phi[kk, iy, ix], 0.0)
    n_parts = int(parts.max()) + 1
    P = np.stack([p[:, parts == q].sum(axis=1) for q in range(n_parts)])  # (parts, velocities, ny, nx)
    F = np.stack([f[:, parts == q].sum(axis=1) for q in range(n_parts)])
    _, score = _score(P, F, FINE_PART, kappa)
    j = int(np.argmax(score))
    v, a, b = np.unravel_index(j, score.shape)
    if score[v, a, b] <= 0:
        return None
    return float(px[b]), float(py[a]), float(vxs[v]), float(vys[v]), float(score[v, a, b]), float(gs[v])


def search_window(maps: PassMaps, sel: np.ndarray, v_min: float, v_max: float, scale_arcsec: float, tier: str,
                  centre: tuple[float, float] | None = None) -> tuple[list[Candidate], dict]:
    """Paths in the visits ``sel`` of a pass with speeds between v_min and
    v_max (arcsec per hour). ``centre`` (ra, dec of the field) turns the
    search for distance on."""
    hours = maps.hours[sel]
    span = float(hours[-1] - hours[0])
    t_ref = float(0.5 * (hours[0] + hours[-1]))
    tau = hours - t_ref
    mjd = maps.mjd0 + hours / 24.0
    v_floor = max(v_min, MIN_SHIFT / span)
    step_c = math.sqrt(2.0) * BLUR_COARSE * scale_arcsec / span  # arcsec per hour (Heinze et al. 2015, eq. 4)
    step_f = math.sqrt(2.0) * BLUR_FINE * scale_arcsec / span
    # how far the path of an object at 1 au bends away from a straight line in this window (pixels), and its straight part
    curve = bend(mjd, centre[0], centre[1], scale_arcsec) if centre is not None else np.zeros((sel.size, 2))
    earth = reflex(mjd, centre[0], centre[1], scale_arcsec) * scale_arcsec if centre is not None else np.zeros(2)  # arcsec/h per 1/au
    sag = float(np.hypot(curve[:, 0], curve[:, 1]).max())
    if sag * GAMMA_MAX > BLUR_COARSE:
        step_g = 2.0 * BLUR_COARSE / sag
        gammas = np.arange(0.0, GAMMA_MAX + 0.5 * step_g, step_g)
    else:
        step_g, gammas = 0.0, np.array([0.0])
    far = tier == "slow" and step_g > 0 and bool(np.any(earth))
    vels = velocity_grid(v_floor, max(v_max, SLOW_FAR_RATE) if far else v_max, step_c)
    info = {"tier": tier, "visits": int(sel.size), "span_h": round(span, 1), "rate_min": round(v_floor, 3), "rate_max": round(v_max, 3),
            "step": round(step_c, 4), "distances": int(gammas.size), "earth": [round(float(v), 1) for v in earth]}
    if len(vels) == 0:
        return [], info
    speed = np.hypot(vels[:, 0], vels[:, 1])
    motions = []
    for g in gammas:
        if far:
            # only what a body of the solar system at this distance can do; beyond FAR_AU that is little, and its
            # motion is mostly Earth's, which may be faster than SLOW_RATE (Makemake, 52 au: 1.5 arcsec per hour)
            ok = bound_velocities(vels, max(0.0, g - 0.5 * step_g), g + 0.5 * step_g, earth, step_c)
            ok &= speed <= (SLOW_FAR_RATE if g - 0.5 * step_g <= FAR_GAMMA else v_max) + 1e-9
        else:
            ok = speed <= v_max + 1e-9
        motions.append(np.column_stack([vels[ok] / (2.0 * scale_arcsec), np.full(int(ok.sum()), g)]))
    motions = np.concatenate(motions)
    info["velocities"] = int(len(motions))
    if len(motions) == 0:
        return [], info
    best, arg, kappa = stack_search(maps.cpsi[sel], maps.cphi[sel], tau, motions, SEED_PART, curve=curve / 2.0)
    peaks = (best >= SEED_SNR) & (best == ndi.maximum_filter(best, size=5))
    ys, xs = np.nonzero(peaks)
    order = np.argsort(-best[ys, xs])[: MAX_SEEDS[tier]]
    info.update(kappa_coarse=round(kappa, 3), seeds=int(len(ys)))
    if order.size == 0:
        return [], info
    # the scale of the full-resolution stacks, from a few of them
    _, _, kappa_f = stack_search(maps.psi[sel], maps.phi[sel], tau, vels[:: max(1, len(vels) // 3)][:3] / scale_arcsec, -np.inf)
    info["kappa_fine"] = round(kappa_f, 3)
    reach = int(math.ceil(0.5 * step_c / step_f)) + 1
    n_g = 0
    if step_g:
        n_g = 1  # the coarse value and one on each side, half a coarse step away; the distance is measured later
    out = []
    for j in order:
        vx, vy, g0 = motions[arg[ys[j], xs[j]]]
        fine = np.clip(g0 + np.arange(-n_g, n_g + 1) * 0.5 * step_g, 0.0, None) if n_g else np.array([0.0])
        r = _refine(maps, sel, t_ref, 2 * int(xs[j]), 2 * int(ys[j]), float(vx) * 2.0, float(vy) * 2.0, step_f / scale_arcsec, reach,
                    kappa_f, np.unique(fine), curve)
        if r is None or r[4] < FINE_SNR:
            continue
        x, y, fvx, fvy, snr, gamma = r
        rate = math.hypot(fvx, fvy) * scale_arcsec
        if rate * span < MIN_SHIFT or rate > (max(v_max, SLOW_FAR_RATE) if far else v_max) + 2 * step_c:
            continue
        out.append(Candidate(x=x, y=y, vx=fvx, vy=fvy, snr=snr, mjd_ref=maps.mjd0 + t_ref / 24.0, first_mjd=float(mjd[0]), last_mjd=float(mjd[-1]),
                             tier=tier, gamma=gamma, curve_mjd=mjd, curve=curve,
                             extra={"seed_snr": round(float(best[ys[j], xs[j]]), 2), "kappa": kappa_f, "sag": sag, "sel": sel,
                                    "step": step_f / scale_arcsec, "earth": earth}))
    info["refined"] = len(out)
    return out, info


def same_path(a: Candidate, b: Candidate, pixels: float = SAME_PIXELS) -> bool:
    """Two paths that stay together over the time they share."""
    first, last = max(a.first_mjd, b.first_mjd), min(a.last_mjd, b.last_mjd)
    if last <= first:
        return False
    for t in (first, last):
        xa, ya = a.at(t)
        xb, yb = b.at(t)
        if math.hypot(float(xa - xb), float(ya - yb)) > pixels:
            return False
    return True


def unique(cands: list[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    for c in sorted(cands, key=lambda c: -c.snr):
        if not any(same_path(c, o) for o in out):
            out.append(c)
    return out


# ----------------------------------------------------------------------------- vetting in the frames
def clipped_stack(a: np.ndarray, sig: np.ndarray, f: np.ndarray, clip=VET_CLIP) -> float:
    """Significance of measurements ``a +- sig`` of one object whose expected
    relative brightness is ``f``, no single one counting for more than
    ``clip`` sigma: what many frames agree on, not what a few shout."""
    ok = np.isfinite(a) & np.isfinite(sig) & (sig > 0)
    if ok.sum() < 3:
        return 0.0
    g = f[ok] / sig[ok]
    z = a[ok] / sig[ok]
    if clip is not None:
        top = np.asarray(clip, dtype=float)[ok] if np.ndim(clip) else clip
        z = np.clip(z, -VET_CLIP, top)
    den = float((g * g).sum())
    return float((g * z).sum()) / math.sqrt(den) if den > 0 else 0.0


def clip_levels(z: np.ndarray, detector: np.ndarray, visit: np.ndarray, base: float = VET_CLIP) -> np.ndarray:
    """How many sigma each frame may weigh: ``base``, or, for an object that
    single frames show, one and a half times what the visits of the same
    detector agree on (the median over at least three visits of each visit's
    mean). One frame never lifts its own limit, nor does one visit: a cosmic
    ray hit or a star's leftover under a slow path stays at ``base``;
    Makemake, at 15 sigma in every frame of the bluest detector, counts in full."""
    out = np.full(z.shape, base, dtype=float)
    for d in np.unique(detector):
        s = (detector == d) & np.isfinite(z)
        means = [float(z[s & (visit == v)].mean()) for v in np.unique(visit[s])]
        if len(means) >= 3:
            out[detector == d] = max(base, 1.5 * float(np.median(means)))
    return out


def _sigma_g(v: np.ndarray) -> float:
    q25, q75 = np.percentile(v, [25, 75])
    return 0.7413 * float(q75 - q25)


def robust_flux(a: np.ndarray, sig: np.ndarray, f: np.ndarray) -> tuple[float, np.ndarray]:
    """Brightness at the template's reference, from the measurements that
    agree with each other (KBMOD's sigma-G filter), and which those are."""
    keep = np.isfinite(a) & np.isfinite(sig) & (sig > 0)
    flux = 0.0
    for _ in range(3):
        if keep.sum() < 3:
            break
        w = f[keep] / sig[keep] ** 2
        den = float((w * f[keep]).sum())
        if den <= 0:
            break
        flux = float((w * a[keep]).sum()) / den
        r = (a - flux * f) / sig
        new = keep & (np.abs(r - np.median(r[keep])) <= SIGMA_G * max(1.0, _sigma_g(r[keep])))
        if new.sum() == keep.sum():
            break
        keep = new
    return flux, keep


def _controls() -> np.ndarray:
    pts = [(0, 0)]
    for k, r in enumerate(CONTROL_RADII):
        for j in range(8):
            ang = (j + 0.5 * k) * math.pi / 4.0
            pts.append((int(round(r * math.cos(ang))), int(round(r * math.sin(ang)))))
    return np.array(pts, dtype=int)


def measure(c: Candidate, cube, frames: list[dict], sky: StaticSky, mjd: np.ndarray, kind: str, margin_h: float = 0.5,
            gamma: float | None = None, controls: bool = True, taken: list | None = None) -> dict | None:
    """Brightness at the path's predicted place in every frame of its window,
    and at the same place on parallel paths (the controls)."""
    h, w = cube.shape[1:]
    offs = _controls() if controls else np.array([(0, 0)])
    reach = int(np.abs(offs).max()) + 2
    idx = np.flatnonzero((mjd >= c.first_mjd - margin_h / 24.0) & (mjd <= c.last_mjd + margin_h / 24.0))
    xs, ys = c.at(mjd[idx], gamma)
    rows = []
    a_all, s_all = [], []
    stamp_num = np.zeros((9, 9))
    stamp_den = np.zeros((9, 9))
    for i, x, y in zip(idx, xs, ys):
        cx, cy = int(round(float(x))), int(round(float(y)))
        if not (reach <= cx < w - reach and reach <= cy < h - reach):
            continue
        if any(math.hypot(float(p[i, 0]) - x, float(p[i, 1]) - y) < AVOID_PIXELS for p in taken or []):
            continue  # a brighter moving object is there in this frame (NaN, where it is not, compares false)
        detector = int(frames[i].get("detector") or 0)
        ref, mask = sky.of(detector)
        y0, y1, x0, x1 = max(0, cy - WINDOW), min(h, cy + WINDOW + 1), max(0, cx - WINDOW), min(w, cx + WINDOW + 1)
        res = np.asarray(cube[i][y0:y1, x0:x1], dtype=np.float32) - ref[y0:y1, x0:x1]
        good = np.isfinite(res) & ~mask[y0:y1, x0:x1]
        if good.sum() < 100:
            continue
        level = float(np.median(res[good]))
        noise = 1.4826 * float(np.median(np.abs(res[good] - level)))
        if not np.isfinite(noise) or noise <= 0:
            continue
        d = np.where(good, res - level, 0.0)
        num = ndi.correlate(d, KERNEL, mode="constant")
        den = ndi.correlate(good.astype(np.float32), KERNEL2, mode="constant")
        py, px = cy - y0 + offs[:, 1], cx - x0 + offs[:, 0]
        ok = good[py, px] & (den[py, px] >= 0.6 * float(KERNEL2.sum()))
        if not ok[0]:
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.where(ok, num[py, px] / den[py, px], np.nan)
            s = np.where(ok, noise / np.sqrt(den[py, px]), np.nan)
        wave = frames[i].get("wave_um")
        wave = float(wave) if wave is not None and np.isfinite(wave) else DETECTOR_UM.get(detector, 2.0)
        rows.append((int(i), float(x), float(y), detector, wave))
        a_all.append(a), s_all.append(s)
        sy, sx = cy - y0, cx - x0
        if 4 <= sy < d.shape[0] - 4 and 4 <= sx < d.shape[1] - 4:
            f = float(template(wave, kind))
            st, sg = d[sy - 4 : sy + 5, sx - 4 : sx + 5], good[sy - 4 : sy + 5, sx - 4 : sx + 5]
            stamp_num += f * np.clip(st / noise, -VET_CLIP, VET_CLIP) / noise
            stamp_den += sg * (f / noise) ** 2
    if len(rows) < MIN_TIMES:
        return None
    return {"rows": rows, "a": np.array(a_all), "sig": np.array(s_all), "stamp": (stamp_num, stamp_den)}


@dataclass
class Patches:
    """What the frames show around a path: each frame's matched-filter value (in sigma) on a small square of pixels."""

    z: np.ndarray  # (frames, 2 reach + 1, 2 reach + 1), NaN where nothing can be measured
    g: np.ndarray  # weight of each frame: expected brightness over noise
    t: np.ndarray  # mjd
    x: np.ndarray  # where the path is in each frame
    y: np.ndarray
    bx: np.ndarray  # the bend per unit of gamma at each frame's time, less the straight line that fits it best in
    by: np.ndarray  # these frames (by weight): changing gamma then changes the bend alone, not place or motion
    line: tuple  # that line: (x at the reference time, x per hour, y, y per hour), per unit of gamma
    gamma: float  # the gamma x and y were computed with
    reach: int
    shown: bool = False  # single frames show the object: three of SIGHT_SNR in two visits


def frame_patches(c: Candidate, cube, frames: list[dict], sky: StaticSky, mjd: np.ndarray, kind: str, reach: int = 2) -> Patches | None:
    h, w = cube.shape[1:]
    idx = np.flatnonzero((mjd >= c.first_mjd - 0.02) & (mjd <= c.last_mjd + 0.02))
    xs, ys = c.at(mjd[idx])
    k2 = float(KERNEL2.sum())
    Z, G, T, X, Y = [], [], [], [], []
    for i, x, y in zip(idx, xs, ys):
        cx, cy = int(round(float(x))), int(round(float(y)))
        if not (reach + 2 <= cx < w - reach - 2 and reach + 2 <= cy < h - reach - 2):
            continue
        detector = int(frames[i].get("detector") or 0)
        ref, mask = sky.of(detector)
        m = max(WINDOW, reach + 2)
        y0, y1, x0, x1 = max(0, cy - m), min(h, cy + m + 1), max(0, cx - m), min(w, cx + m + 1)
        res = np.asarray(cube[i][y0:y1, x0:x1], dtype=np.float32) - ref[y0:y1, x0:x1]
        good = np.isfinite(res) & ~mask[y0:y1, x0:x1]
        if good.sum() < 100:
            continue
        level = float(np.median(res[good]))
        noise = 1.4826 * float(np.median(np.abs(res[good] - level)))
        if not np.isfinite(noise) or noise <= 0:
            continue
        sy, sx = slice(cy - y0 - reach - 1, cy - y0 + reach + 2), slice(cx - x0 - reach - 1, cx - x0 + reach + 2)
        d = np.where(good, res - level, 0.0)[sy, sx]
        g = good[sy, sx].astype(np.float32)
        if d.shape != (2 * reach + 3, 2 * reach + 3):
            continue
        num = ndi.correlate(d, KERNEL, mode="constant")[1:-1, 1:-1]
        den = ndi.correlate(g, KERNEL2, mode="constant")[1:-1, 1:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.where(den >= 0.6 * k2, num / np.sqrt(den) / noise, np.nan)
        wave = frames[i].get("wave_um")
        wave = float(wave) if wave is not None and np.isfinite(wave) else DETECTOR_UM.get(detector, 2.0)
        Z.append(z), G.append(float(template(wave, kind)) / noise), T.append(float(mjd[i])), X.append(float(x)), Y.append(float(y))
    if len(Z) < MIN_TIMES:
        return None
    T = np.array(T)
    Z, G = np.array(Z), np.array(G)
    line = (0.0, 0.0, 0.0, 0.0)
    if c.curve is not None:
        bx, by = np.interp(T, c.curve_mjd, c.curve[:, 0]), np.interp(T, c.curve_mjd, c.curve[:, 1])
        tau = (T - c.mjd_ref) * 24.0
        wgt = G * np.isfinite(Z[:, reach, reach])  # polyfit squares its weights
        if (wgt > 0).sum() >= 3 and np.ptp(tau[wgt > 0]) > 0:
            fx, fy = np.polyfit(tau, bx, 1, w=wgt), np.polyfit(tau, by, 1, w=wgt)
            bx, by = bx - np.polyval(fx, tau), by - np.polyval(fy, tau)
            line = (float(fx[1]), float(fx[0]), float(fy[1]), float(fy[0]))
    else:
        bx, by = np.zeros(T.size), np.zeros(T.size)
    seen = np.nan_to_num(Z[:, max(0, reach - 2) : reach + 3, max(0, reach - 2) : reach + 3], nan=-np.inf).reshape(len(Z), -1).max(axis=1) >= SIGHT_SNR
    shown = bool(seen.sum() >= 3 and np.unique(_by_visit(T)[seen]).size >= 2)
    return Patches(z=Z, g=G, t=T, x=np.array(X), y=np.array(Y), bx=bx, by=by, line=line, gamma=float(c.gamma), reach=reach, shown=shown)


def fit_patches(p: Patches, c: Candidate, gamma: float | None = None, shifts: int = 1, steps: int = 2) -> tuple[float, tuple]:
    """The best stack of the frames over small changes of a path: position by
    up to ``shifts`` pixels, motion by up to ``steps`` fine steps, at a given
    gamma. A frame counts up to VET_CLIP sigma, or up to 15 for an object
    that single frames show. Returns the significance (before the window's
    noise factor) and (dx, dy, dvx, dvy)."""
    top = 15.0 if p.shown else VET_CLIP
    tau = (p.t - c.mjd_ref) * 24.0
    step = float(c.extra.get("step") or 0.0)
    dg = 0.0 if gamma is None else gamma - p.gamma
    X, Y = p.x + dg * p.bx, p.y + dg * p.by
    rx, ry = np.rint(p.x), np.rint(p.y)
    rows = np.arange(len(p.z))
    need = max(MIN_TIMES, int(0.7 * np.isfinite(p.z[:, p.reach, p.reach]).sum()))
    best, arg = 0.0, (0.0, 0.0, 0.0, 0.0)
    moves = sorted(np.arange(-shifts, shifts + 1.0), key=abs)  # no change first: it wins a tie
    for dvx in sorted(np.arange(-steps, steps + 1) * step, key=abs):
        for dvy in sorted(np.arange(-steps, steps + 1) * step, key=abs):
            for dx in moves:
                for dy in moves:
                    ox = np.rint(X + dx + dvx * tau - rx).astype(int)
                    oy = np.rint(Y + dy + dvy * tau - ry).astype(int)
                    inside = (np.abs(ox) <= p.reach) & (np.abs(oy) <= p.reach)
                    z = p.z[rows, np.clip(oy, -p.reach, p.reach) + p.reach, np.clip(ox, -p.reach, p.reach) + p.reach]
                    use = inside & np.isfinite(z)
                    if use.sum() < need:
                        continue
                    score = float((p.g[use] * np.clip(z[use], -VET_CLIP, top)).sum()) / math.sqrt(float((p.g[use] ** 2).sum()))
                    if score > best + 1e-9:
                        best, arg = score, (float(dx), float(dy), float(dvx), float(dvy))
    return best, arg


def polish(c: Candidate, cube, frames: list[dict], sky: StaticSky, mjd: np.ndarray, kind: str) -> bool:
    """For an object that single frames show: move the path, by a pixel and
    two steps of the motion at most, to where the frames have it.

    The search limits every frame to CLIP sigma, which is right for what no
    frame shows and blunt for what several do: a bright object then looks
    the same on neighbouring paths, and the one picked may run a pixel
    beside it. Here the frames count up to 15 sigma. Nothing is done unless
    three frames of two visits show a source of SIGHT_SNR near the path.
    Returns whether the path was changed."""
    p = frame_patches(c, cube, frames, sky, mjd, kind, reach=2)
    if p is None:
        return False
    if not p.shown:
        return False
    _, arg = fit_patches(p, c)
    if arg == (0.0, 0.0, 0.0, 0.0):
        return False
    c.x, c.y, c.vx, c.vy = c.x + arg[0], c.y + arg[1], c.vx + arg[2], c.vy + arg[3]
    return True


def distance_from_frames(c: Candidate, cube, frames: list[dict], sky: StaticSky, mjd: np.ndarray, kind: str) -> dict:
    """The distance from how the path bends, measured in the frames: the best
    stack at each of a series of distances (position and motion are free
    each time), the best distance, and the range that is not clearly worse.
    A stack's significance squared is twice the log-likelihood, so a drop of
    4 in it would be two sigma. On Haumea and Makemake that range missed the
    true distance by a little (whole pixels, colour, frames that are not
    independent), so the range given is a drop of RANGE_DROP = 9.
    Changes the candidate to the best path.

    The visit maps of the search are not used for this: they limit a frame
    to CLIP sigma, which flattens the peak of an object that frames show and
    then favours paths that are too straight (Haumea came out at 138 au
    instead of 50)."""
    sag = float(c.extra.get("sag") or 0.0)
    if c.curve is None or sag * GAMMA_MAX < 1.5:  # the window is too short for the bend to show, even for the nearest body
        return {}
    reach = int(min(12, math.ceil(GAMMA_MAX * sag) + 2))
    p = frame_patches(c, cube, frames, sky, mjd, kind, reach=reach)
    if p is None:
        return {}
    step = 0.5 / sag  # half a pixel of bend
    gammas = np.arange(0.0, GAMMA_MAX + step, step)
    kappa = max(1.0, float(c.extra.get("kappa") or 1.0))
    fits = [fit_patches(p, c, float(g)) for g in gammas]
    score = np.array([f[0] for f in fits]) / kappa
    j = int(np.argmax(score))
    if score[j] <= 0:
        return {}
    near = np.flatnonzero(score**2 >= score[j] ** 2 - RANGE_DROP)
    lo, hi = float(gammas[near.min()]), float(gammas[near.max()])
    dx, dy, dvx, dvy = fits[j][1]
    dg = float(gammas[j]) - p.gamma  # the straight part of the bend's change goes into place and motion
    c.x, c.vx = c.x + dx - dg * p.line[0], c.vx + dvx - dg * p.line[1]
    c.y, c.vy = c.y + dy - dg * p.line[2], c.vy + dvy - dg * p.line[3]
    c.gamma = float(gammas[j])
    out = {"gamma": round(float(gammas[j]), 5), "gamma_lo": round(max(0.0, lo - 0.5 * step), 5), "gamma_hi": round(hi + 0.5 * step, 5),
           "bend_pixels_at_1au": round(sag, 1), "from": "frames"}
    out["distance_au"] = round(1.0 / float(gammas[j]), 1) if gammas[j] > 0 else None
    out["distance_min_au"] = round(1.0 / out["gamma_hi"], 1)
    out["distance_max_au"] = round(1.0 / out["gamma_lo"], 1) if out["gamma_lo"] > 0 else None
    return out


def _stamp_test(num: np.ndarray, den: np.ndarray) -> dict:
    with np.errstate(divide="ignore", invalid="ignore"):
        st = np.where(den > 0, num / np.sqrt(den), 0.0)
    core = st[2:7, 2:7]
    jy, jx = np.unravel_index(int(np.argmax(core)), core.shape)
    py, px = 2 + int(jy), 2 + int(jx)
    peak = float(st[py, px])
    off = math.hypot(py - 4, px - 4)
    nine = float(st[max(0, py - 1) : py + 2, max(0, px - 1) : px + 2].sum())
    ring = [st[py + dy, px + dx] for dy in range(-3, 4) for dx in range(-3, 4)
            if max(abs(dy), abs(dx)) == 3 and 0 <= py + dy < 9 and 0 <= px + dx < 9]
    ring_level = float(np.median(ring)) if ring else 0.0
    ok = peak > 0 and off <= STAMP_OFF and nine >= STAMP_CORE_MIN * peak and ring_level <= STAMP_RING_MAX * peak
    return {"ok": bool(ok), "peak": round(peak, 2), "offset": round(off, 2), "core": round(nine / peak, 2) if peak > 0 else 0.0,
            "ring": round(ring_level / peak, 2) if peak > 0 else 0.0}


def _distance_bright(c: Candidate, cube, frames: list[dict], sky: StaticSky, rows: list) -> dict:
    """The distance of an object that shows in single frames, from where it
    is in each of them: a straight line plus the bend of Earth's motion,
    fitted to the centroids (position, motion and 1/distance: linear least
    squares). Far more exact than the stacks, whose pixels are whole and
    whose values are limited. Changes the candidate to the fitted path."""
    sag = float(c.extra.get("sag") or 0.0)
    if c.curve is None or sag * GAMMA_MAX < 1.5:
        return {}
    h, w = cube.shape[1:]
    t, px, py = [], [], []
    for i, x, y, detector, _ in rows:
        cx, cy = int(round(x)), int(round(y))
        if not (9 <= cx < w - 9 and 9 <= cy < h - 9):
            continue
        ref, mask = sky.of(detector)
        box = np.asarray(cube[i][cy - 9 : cy + 10, cx - 9 : cx + 10], dtype=np.float32) - ref[cy - 9 : cy + 10, cx - 9 : cx + 10]
        good = np.isfinite(box) & ~mask[cy - 9 : cy + 10, cx - 9 : cx + 10]
        far = good.copy()
        far[5:14, 5:14] = False
        if far.sum() < 60:
            continue
        level = float(np.median(box[far]))
        noise = 1.4826 * float(np.median(np.abs(box[far] - level)))
        # the mask of the static sky is not applied here: the stars are subtracted, and in a crowded field the
        # mask would leave too few frames; what a star leaves behind is an outlier of the fit, and dropped there
        fin = np.isfinite(box)
        core = np.where(fin[7:12, 7:12], box[7:12, 7:12] - level, -np.inf)
        jy, jx = np.unravel_index(int(np.argmax(core)), core.shape)
        if noise <= 0 or core[jy, jx] < 5.0 * noise:
            continue
        qy, qx = 7 + int(jy), 7 + int(jx)
        if not fin[qy - 1 : qy + 2, qx - 1 : qx + 2].all():
            continue
        v = np.clip(box[qy - 1 : qy + 2, qx - 1 : qx + 2] - level, 0.0, None)
        gy, gx = np.mgrid[-1:2, -1:2]
        t.append(float(frames[i]["mjd"]))
        px.append(cx - 9 + qx + float((v * gx).sum() / v.sum()))
        py.append(cy - 9 + qy + float((v * gy).sum() / v.sum()))
    t, px, py = np.array(t), np.array(px), np.array(py)
    if t.size < 10 or _by_visit(t).max() + 1 < 5:
        return {}
    tau = (t - c.mjd_ref) * 24.0
    bx, by = np.interp(t, c.curve_mjd, c.curve[:, 0]), np.interp(t, c.curve_mjd, c.curve[:, 1])
    one, zero = np.ones(t.size), np.zeros(t.size)
    A = np.vstack([np.column_stack([one, tau, zero, zero, bx]), np.column_stack([zero, zero, one, tau, by])])
    b = np.concatenate([px, py])
    keep = np.ones(b.size, dtype=bool)
    sol = None
    for _ in range(3):
        sol, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        r = b - A @ sol
        scatter = max(0.05, 1.4826 * float(np.median(np.abs(r[keep]))))
        new = np.abs(r) <= 3.0 * scatter
        new = new[: t.size] & new[t.size :]
        new = np.concatenate([new, new])
        if new.sum() == keep.sum():
            break
        keep = new
    if keep.sum() < 20:
        return {}
    cov = np.linalg.inv(A[keep].T @ A[keep]) * float((r[keep] ** 2).sum()) / max(1, int(keep.sum()) - 5)
    gamma, err = float(sol[4]), float(math.sqrt(cov[4, 4]))
    if not np.isfinite(err) or err <= 0 or gamma < -3.0 * err or gamma > 2.0 * GAMMA_MAX:
        return {}
    c.x, c.vx, c.y, c.vy, c.gamma = float(sol[0]), float(sol[1]), float(sol[2]), float(sol[3]), max(0.0, gamma)
    lo, hi = max(0.0, gamma - 2.0 * err), max(0.0, gamma + 2.0 * err)
    return {"gamma": round(max(0.0, gamma), 5), "gamma_lo": round(lo, 5), "gamma_hi": round(hi, 5), "bend_pixels_at_1au": round(sag, 1),
            "from": "centroids", "frames": int(keep.sum() // 2), "scatter_arcsec": None,
            "distance_au": round(1.0 / gamma, 1) if gamma > 0 else None,
            "distance_min_au": round(1.0 / hi, 1) if hi > 0 else None,
            "distance_max_au": round(1.0 / lo, 1) if lo > 0 else None}


def possible(c: Candidate, distance: dict, scale_arcsec: float) -> bool:
    """Can a body on a closed orbit around the Sun move like this at the
    distance its path's bend allows? Its motion less Earth's part at that
    distance is its own, and that cannot exceed the speed of escape (with
    some slack: the distance is not known well)."""
    earth = c.extra.get("earth")
    if earth is None or not distance or not np.any(earth):
        return True
    lo, hi = float(distance.get("gamma_lo") or 0.0), float(distance.get("gamma_hi") or GAMMA_MAX)
    g = np.linspace(lo, max(hi, lo), 60)
    own = np.hypot(c.vx * scale_arcsec - g * earth[0], c.vy * scale_arcsec - g * earth[1])
    return bool(np.any(own <= BOUND_SLACK * ESCAPE * g**1.5 + 2.0 * float(c.extra.get("step") or 0.0) * scale_arcsec))


def rest_stack(c: Candidate, cube, frames: list[dict], sky: StaticSky, rows: list, kind: str, half: int = 2) -> float:
    """The best stack without motion at the start, the middle and the end of
    the path, in the same frames, the mask of the static sky ignored.

    A star that is brighter during this pass than in the others (a variable
    star, a nova, a supernova) is not in the static sky. Its core is masked
    when a faint source is known there, and what is left, a ring of light
    around the mask, can pass for something that crawls along the ring.
    Without the mask the star stands where it is, far brighter at rest than
    on any path. An object that moves is at one place in part of the frames only.
    """
    h, w = cube.shape[1:]
    xs, ys = np.array([r[1] for r in rows]), np.array([r[2] for r in rows])
    order = np.argsort([float(frames[r[0]]["mjd"]) for r in rows])
    best = 0.0
    for j in (order[0], order[len(order) // 2], order[-1]):
        cx, cy = int(round(float(xs[j]))), int(round(float(ys[j])))
        if not (half + 2 <= cx < w - half - 2 and half + 2 <= cy < h - half - 2):
            continue
        num = np.zeros((2 * half + 1, 2 * half + 1))
        den = np.zeros_like(num)
        y0, y1, x0, x1 = max(0, cy - WINDOW), min(h, cy + WINDOW + 1), max(0, cx - WINDOW), min(w, cx + WINDOW + 1)
        for i, _, _, detector, wave in rows:
            ref, mask = sky.of(detector)
            res = np.asarray(cube[i][y0:y1, x0:x1], dtype=np.float32) - ref[y0:y1, x0:x1]
            fin = np.isfinite(res)
            good = fin & ~mask[y0:y1, x0:x1]
            if good.sum() < 100:
                continue
            level = float(np.median(res[good]))
            noise = 1.4826 * float(np.median(np.abs(res[good] - level)))
            if not np.isfinite(noise) or noise <= 0:
                continue
            a = np.where(fin, res - level, 0.0)[cy - y0 - half - 1 : cy - y0 + half + 2, cx - x0 - half - 1 : cx - x0 + half + 2]
            k = fin[cy - y0 - half - 1 : cy - y0 + half + 2, cx - x0 - half - 1 : cx - x0 + half + 2].astype(np.float32)
            if a.shape != (2 * half + 3, 2 * half + 3):
                continue
            n = ndi.correlate(a, KERNEL, mode="constant")[1:-1, 1:-1]
            d = ndi.correlate(k, KERNEL2, mode="constant")[1:-1, 1:-1]
            ok = d >= 0.6 * float(KERNEL2.sum())
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(ok, np.clip(n / np.sqrt(d) / noise, -VET_CLIP, VET_CLIP), 0.0)
            g = float(template(wave, kind)) * np.sqrt(d) / noise * ok
            num += g * z
            den += g * g
        with np.errstate(divide="ignore", invalid="ignore"):
            st = np.where(den > 0, num / np.sqrt(den), 0.0)
        best = max(best, float(st.max()))
    return best


def on_off(c: Candidate, cube, frames: list[dict], sky: StaticSky, rows: list, margin: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Each frame's brightness at the predicted place, minus what the same
    pixel of the sky shows in the frames of the same detector taken while the
    object is elsewhere on its path.

    The leftover of a star, the halo of a bright one, a galaxy the median
    missed: all of them are there whether the object is or not, and cancel.
    An object that moves is in a pixel at some times only, and stays. A frame
    whose pixel is never seen without the object cannot be judged (NaN).
    """
    h, w = cube.shape[1:]
    m = len(rows)
    idx = np.array([r[0] for r in rows])
    pos = np.array([[r[1], r[2]] for r in rows])
    det = np.array([r[3] for r in rows])
    px, py = np.rint(pos[:, 0]).astype(int), np.rint(pos[:, 1]).astype(int)
    x0, x1 = max(0, int(px.min()) - margin), min(w, int(px.max()) + margin + 1)
    y0, y1 = max(0, int(py.min()) - margin), min(h, int(py.max()) + margin + 1)
    A = np.full((m, m), np.nan, dtype=np.float32)  # frame j at the place of frame i
    S = np.full((m, m), np.nan, dtype=np.float32)
    k2 = float(KERNEL2.sum())
    for j, i in enumerate(idx):
        ref, mask = sky.of(int(det[j]))
        res = np.asarray(cube[i][y0:y1, x0:x1], dtype=np.float32) - ref[y0:y1, x0:x1]
        good = np.isfinite(res) & ~mask[y0:y1, x0:x1]
        if good.sum() < 100:
            continue
        level = float(np.median(res[good]))
        noise = 1.4826 * float(np.median(np.abs(res[good] - level)))
        if not np.isfinite(noise) or noise <= 0:
            continue
        num = ndi.correlate(np.where(good, res - level, 0.0), KERNEL, mode="constant")
        den = ndi.correlate(good.astype(np.float32), KERNEL2, mode="constant")
        d, n = den[py - y0, px - x0], num[py - y0, px - x0]
        ok = good[py - y0, px - x0] & (d >= 0.6 * k2)
        with np.errstate(divide="ignore", invalid="ignore"):
            A[j] = np.where(ok, n / d, np.nan)
            S[j] = np.where(ok, noise / np.sqrt(d), np.nan)
    dist = np.hypot(pos[:, None, 0] - pos[None, :, 0], pos[:, None, 1] - pos[None, :, 1])
    same = det[:, None] == det[None, :]
    diff = np.full(m, np.nan)
    sig = np.full(m, np.nan)
    for i in range(m):
        if not np.isfinite(A[i, i]):
            continue
        away = (dist[:, i] >= OFF_PIXELS) & np.isfinite(A[:, i])
        off = away & same[:, i]
        if off.sum() < OFF_MIN:  # the detector did not look long enough: any detector then (a leftover shows in most)
            off = away
        if off.sum() < OFF_MIN:
            continue
        on = int((same[:, i] & (dist[:, i] < 1.0) & np.isfinite(A[:, i])).sum())
        diff[i] = float(A[i, i]) - float(np.median(A[off, i]))
        sig[i] = float(S[i, i]) * math.sqrt(1.0 + 1.57 * on / int(off.sum()))
    return diff, sig


def _by_visit(t: np.ndarray) -> np.ndarray:
    """Index of the visit of each time."""
    order = np.argsort(t)
    out = np.zeros(t.size, dtype=int)
    k, start = 0, t[order[0]]
    for i in order:
        if (t[i] - start) * 1440.0 > VISIT_GAP_MIN:
            k, start = k + 1, t[i]
        out[i] = k
    return out


def vet(c: Candidate, cube, frames: list[dict], sky: StaticSky, mjd: np.ndarray, kind: str, maps: PassMaps | None = None,
        scale_arcsec: float = 6.15, taken: list | None = None, moat: np.ndarray | None = None) -> tuple[dict | None, str]:
    """Judge a path in the frames. Returns (evidence, "") or (None, why not).

    ``taken``: for each brighter moving object, its pixel position in every
    frame (NaN where it is not in the field): frames in which it is near the
    path are left out, so that no path is made of another object's light.
    """
    m = measure(c, cube, frames, sky, mjd, kind, taken=taken)
    if m is None:
        return None, "few_frames"
    rows = m["rows"]
    idx = np.array([r[0] for r in rows])
    f = template(np.array([r[4] for r in rows]), kind)
    a, sig = m["a"], m["sig"]
    n = len(rows)
    dets = np.array([r[3] for r in rows])
    t = mjd[idx]
    visit = _by_visit(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        top = clip_levels(a[:, 0] / sig[:, 0], dets, visit)
    plain = clipped_stack(a[:, 0], sig[:, 0], f, top)
    control = np.array([clipped_stack(a[:, j], sig[:, j], f) for j in range(1, a.shape[1]) if np.isfinite(a[:, j]).sum() >= 0.6 * n])
    # The scale of the significance. Pixels of a resampled frame are not independent and all frames of a detector
    # share one static sky, so a stack scatters more than its frames say: by the factor measured on the stacks of
    # this window (kappa, from all pixels). The parallel paths can only raise it: sixteen of them are too few to
    # lower it, and a path picked because it stands out would pick neighbours that happen to be quiet.
    floor = max(1.0, float(c.extra.get("kappa") or 1.3))
    if control.size >= 6:
        centre = max(0.0, float(np.median(control)))
        width = max(floor, 1.4826 * float(np.median(np.abs(control - np.median(control)))))
    else:
        centre, width = 0.0, floor
    if (plain - centre) / width < MIN_SNR:
        return None, "faint"
    x0, y0 = c.at(t.min())
    x1, y1 = c.at(t.max())
    moved = math.hypot(float(x1 - x0), float(y1 - y0))
    if moved * scale_arcsec < MIN_SHIFT:
        return None, "no_motion"
    stamp = _stamp_test(*m["stamp"])
    if not stamp["ok"]:
        return None, "not_compact"
    # the same measurements, each against its own pixel without the object
    diff, dsig = on_off(c, cube, frames, sky, rows)
    ok = np.isfinite(diff)
    if ok.sum() < MIN_TIMES or np.unique(np.round(t[ok], 4)).size < MIN_TIMES:
        return None, "few_times"
    with np.errstate(divide="ignore", invalid="ignore"):
        zd = diff / dsig
        top = clip_levels(zd, dets, visit)
    snr = clipped_stack(diff, dsig, f, top) / width
    if snr < MIN_SNR:
        return None, "static"
    if moat is not None:
        # away from bright stars the path must hold as well
        h, w = moat.shape
        px = np.clip(np.rint([r[1] for r in rows]).astype(int), 0, w - 1)
        py = np.clip(np.rint([r[2] for r in rows]).astype(int), 0, h - 1)
        clear = ok & ~moat[py, px]
        weight = np.where(ok, (f / np.where(ok, dsig, 1.0)) ** 2, 0.0)
        share = float(weight[clear].sum() / weight.sum()) if weight.sum() > 0 else 0.0
        if share < MOAT_CLEAR:
            return None, "near_star"
        away = clipped_stack(np.where(clear, diff, np.nan), dsig, f, top) / width
        if away < min(MOAT_ENOUGH, MOAT_SIGNAL * snr * math.sqrt(share)):
            return None, "near_star"
    # every visit by itself: most must show the object a little, a few visits cannot carry a path
    per_visit = np.array([clipped_stack(diff[visit == v], dsig[visit == v], f[visit == v], top[visit == v]) / width
                          if (ok & (visit == v)).sum() >= 1 else np.nan for v in range(visit.max() + 1)])
    # clipped_stack wants three measurements; a visit of one or two frames is judged on their mean
    for v in np.flatnonzero(np.isnan(per_visit) | (per_visit == 0)):
        s = ok & (visit == v)
        if s.any():
            g = f[s] / dsig[s]
            per_visit[v] = float((g * np.clip(diff[s] / dsig[s], -VET_CLIP, top[s])).sum()) / math.sqrt(float((g * g).sum())) / width
    judged = np.isfinite(per_visit)
    n_visits = int(judged.sum())
    positive = int((per_visit[judged] > 0).sum())
    visits_seen = int((per_visit[judged] >= VISIT_SNR).sum())
    if n_visits < MIN_STACK_VISITS or visits_seen < MIN_VISITS_SEEN:
        return None, "few_visits"
    # An object that single frames show (5 sigma, above what the same pixel shows without it) in three visits or
    # more, over a day and a half or more, has shown that it moves along this path. It need not show at every
    # wavelength: Makemake, bright at 1 micron, is dark beyond 1.5 (methane ice), so the visits and the thirds of
    # the time span in which only the long wavelengths looked show nothing, and say nothing against it.
    sight = ok & (zd >= SIGHT_SNR)
    sight_visits = np.unique(visit[sight])
    sighted = bool(sight_visits.size >= MIN_VISITS_SEEN and (t[sight].max() - t[sight].min()) * 24.0 >= MIN_SPAN_H)
    if positive < VISITS_POSITIVE * n_visits and not sighted:
        return None, "uneven"
    # each third of the frames, by weight, must show the object
    order = np.argsort(t[ok])
    g = (f[ok] / dsig[ok])[order]
    z = np.clip(diff[ok] / dsig[ok], -VET_CLIP, top[ok])[order]
    wk = np.cumsum(g * g)
    part = np.minimum((wk / wk[-1] * THIRDS - 1e-9).astype(int), THIRDS - 1)
    parts = []
    for q in range(THIRDS):
        s = part == q
        den = float((g[s] ** 2).sum())
        parts.append(float((g[s] * z[s]).sum()) / math.sqrt(den) / width if den > 0 else 0.0)
    if min(parts) < max(PART_SNR, PART_SHARE * snr / math.sqrt(THIRDS)) and not sighted:
        return None, "one_part"
    if maps is not None and _at_rest(c, maps) / float(c.extra.get("kappa") or 1.0) > STATIC_SHARE * c.snr:
        return None, "at_rest"
    if rest_stack(c, cube, frames, sky, rows, kind) / width > STATIC_SHARE * snr:
        return None, "at_rest"
    flux, keep = robust_flux(np.where(ok, diff, a[:, 0]), np.where(ok, dsig, sig[:, 0]), f)
    zz = a[:, 0] / sig[:, 0]
    bright = bool(np.median(zz) > VET_CLIP)
    return {"snr": round(snr, 2), "snr_plain": round((plain - centre) / width, 2), "control_width": round(width, 2),
            "flux": flux, "keep": keep, "rows": rows, "z": zz, "a": a[:, 0], "parts": [round(p, 2) for p in parts],
            "moved_pixels": round(moved, 1), "stamp": stamp, "frames": int(ok.sum()), "bright": bright, "visits": n_visits,
            "visits_positive": positive, "visits_seen": visits_seen, "sighted_visits": int(sight_visits.size)}, ""


def _at_rest(c: Candidate, maps: PassMaps) -> float:
    """The best stack without motion at the start, the middle and the end of the path, in the visit maps."""
    t = maps.mjd0 + maps.hours / 24.0
    sel = (t >= c.first_mjd - 1e-6) & (t <= c.last_mjd + 1e-6)
    _, h, w = maps.psi.shape
    best = 0.0
    for when in (c.first_mjd, c.mjd_ref, c.last_mjd):
        x, y = c.at(when)
        cx, cy = int(round(float(x))), int(round(float(y)))
        if not (0 <= cx < w and 0 <= cy < h):
            continue
        p, f = float(maps.psi[sel, cy, cx].sum()), float(maps.phi[sel, cy, cx].sum())
        if f > 0:
            best = max(best, p / math.sqrt(f))
    return best


# ----------------------------------------------------------------------------- results
def _track(c: Candidate, ev: dict, frames: list[dict], wcs, scale_arcsec: float, kind: str) -> Track:
    to_mjy = (scale_arcsec / ARCSEC) ** 2 * 1e9
    rows, keep, z, a = ev["rows"], ev["keep"], ev["z"], ev["a"]
    xs = np.array([r[1] for r in rows])
    ys = np.array([r[2] for r in rows])
    ra, dec = (np.atleast_1d(v) for v in wcs.pixel_to_world_values(xs, ys))
    points, dets = [], []

    def detection(j: int) -> Detection:
        i, x, y, detector, wave = rows[j]
        f = frames[i]
        return Detection(exposure=str(f["exposure"]), mjd=float(f["mjd"]), ra=float(ra[j]) % 360.0, dec=float(dec[j]), snr=float(z[j]),
                         flux_mjy=float(a[j]) * to_mjy, npix=9, detector=detector, wave_um=wave, x=float(x), y=float(y), frame=int(i))

    for j, (i, x, y, detector, wave) in enumerate(rows):
        f = frames[i]
        seen = bool(z[j] >= SEEN_SNR)
        points.append({"mjd": round(float(f["mjd"]), 6), "exposure": str(f["exposure"]), "detector": detector, "wave_um": round(wave, 4),
                       "ra": round(float(ra[j]) % 360.0, 6), "dec": round(float(dec[j]), 6), "snr": round(float(z[j]), 2),
                       "core_snr": round(float(z[j]), 2), "seen": seen, "flux_mjy": round(float(a[j]) * to_mjy, 4), "on_static": False,
                       "counted": True})
        if seen:
            dets.append(detection(j))
    if len(dets) < 4:  # a track needs detections to show: the frames in which the object is least faint
        have = {d.frame for d in dets}
        dets += [detection(int(j)) for j in np.argsort(-z)[:4] if rows[int(j)][0] not in have]
    dets.sort(key=lambda d: d.mjd)
    t_all = np.array([p["mjd"] for p in points])
    times_covered = int(np.unique(np.round(t_all, 4)).size)
    times_seen = int(np.unique(np.round([p["mjd"] for p in points if p["seen"]], 4)).size)
    hours = (np.array([d.mjd for d in dets]) - dets[0].mjd) * 24.0
    flux_mjy = ev["flux"] * to_mjy  # at the template's reference: 1 micron for "sun"
    # the straight line that fits the path best over the frames (the bend is kept in the points)
    th = (t_all - c.mjd_ref) * 24.0
    px, py = np.polyfit(th, xs, 1), np.polyfit(th, ys, 1)
    ra0, dec0 = wcs.pixel_to_world_values(float(px[1]), float(py[1]))
    line = np.hypot(xs - np.polyval(px, th), ys - np.polyval(py, th)) * scale_arcsec
    curved = {}
    if "a_east" in Track.__dataclass_fields__ and line.max() > 0.25 * scale_arcsec and np.unique(np.round(th, 2)).size >= 6:
        # the bend, as a constant change of the motion: position, motion and its change at the reference time
        qx, qy = np.polyfit(th, xs, 2), np.polyfit(th, ys, 2)
        ra0, dec0 = wcs.pixel_to_world_values(float(qx[2]), float(qy[2]))
        px, py = qx[1:], qy[1:]
        curved = {"a_east": -2.0 * float(qx[0]) * scale_arcsec, "a_north": 2.0 * float(qy[0]) * scale_arcsec}
    t = Track(detections=dets, mjd0=float(c.mjd_ref), ra0=float(ra0) % 360.0, dec0=float(dec0), v_east=-float(px[0]) * scale_arcsec,
              v_north=float(py[0]) * scale_arcsec, rms_arcsec=round(scale_arcsec * 1.5 / max(ev["snr"], 1.0), 2),
              n_times=int(np.unique(np.round(hours, 4)).size), n_visits=movers._visits(hours), **curved)
    seen = sum(bool(p["seen"]) for p in points)
    t.extra.update(
        method="stack", stack_snr=ev["snr"], stack_snr_search=ev.get("search_snr"), stack_snr_plain=ev["snr_plain"], stack_frames=ev["frames"], stack_times=times_covered,
        stack_parts=ev["parts"], stack_template=kind, stack_flux_mjy=round(float(flux_mjy), 5),
        stack_mag_ab=round(8.9 - 2.5 * math.log10(flux_mjy * 1e-3), 2) if flux_mjy > 0 else None,
        stack_span_h=round((c.last_mjd - c.first_mjd) * 24.0, 1), stack_tier=c.tier, stack_stamp=ev["stamp"],
        stack_moved_arcsec=round(ev["moved_pixels"] * scale_arcsec, 1), stack_bend_arcsec=round(float(line.max()), 1),
        stack_strong=bool(ev["snr"] >= STRONG_SNR), stack_distance=ev.get("distance") or {}, stack_visits=ev["visits"],
        stack_visits_positive=ev["visits_positive"], visits_seen=ev["visits_seen"], stack_sighted_visits=ev["sighted_visits"],
        covered=len(points), seen=seen, seen_fraction=round(seen / len(points), 3) if points else None,
        times_seen=times_seen, times_covered=times_covered, points=points)
    return t


def _known(c: Candidate, exclude: list[Track], wcs, scale_arcsec: float) -> bool:
    """Is this path one of the tracks found already (by the linker)?"""
    for t in exclude or []:
        first = min(d.mjd for d in t.detections) - 1.0
        last = max(d.mjd for d in t.detections) + 1.0
        if last < c.first_mjd or first > c.last_mjd:
            continue
        if math.hypot(t.v_east + c.vx * scale_arcsec, t.v_north - c.vy * scale_arcsec) > max(1.0, 0.15 * t.rate):
            continue
        ra, dec = t.at(c.mjd_ref)
        x, y = wcs.world_to_pixel_values(float(ra), float(dec))
        cx, cy = c.at(c.mjd_ref)
        if math.hypot(float(x) - float(cx), float(y) - float(cy)) * scale_arcsec <= 2.0 * movers.MERGE_SEP:
            return True
    return False


def positions(track: Track, frames: list[dict], wcs, mjd: np.ndarray | None = None) -> np.ndarray:
    """Where a track's object is in each frame: pixels (x, y), NaN in frames
    it is not known in. Frames the track was measured in are found by their
    exposure, so this holds when the times of the frames have been shuffled;
    with ``mjd`` the frames in between are filled in from the track's path."""
    out = np.full((len(frames), 2), np.nan)
    if mjd is not None and track.detections:
        first, last = min(d.mjd for d in track.detections) - 0.1, max(d.mjd for d in track.detections) + 0.1
        ra, dec = track.at(mjd)
        x, y = wcs.world_to_pixel_values(np.asarray(ra, dtype=float), np.asarray(dec, dtype=float))
        inside = (mjd >= first) & (mjd <= last)
        out[inside, 0], out[inside, 1] = np.asarray(x)[inside], np.asarray(y)[inside]
    index = {str(f["exposure"]): i for i, f in enumerate(frames)}
    pts = [(index[p["exposure"]], p["ra"], p["dec"]) for p in track.extra.get("points") or [] if p.get("exposure") in index]
    pts += [(index[d.exposure], d.ra, d.dec) for d in track.detections if d.exposure in index]
    if pts:
        i, ra, dec = (np.array(v) for v in zip(*pts))
        x, y = wcs.world_to_pixel_values(ra.astype(float), dec.astype(float))
        out[i.astype(int), 0], out[i.astype(int), 1] = x, y
    return out


def _windows(hours: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """The windows of a pass: the whole of it for slow motions (two halves
    that overlap when it is very long), stretches of a few days for faster ones."""
    out = []
    k = hours.size
    span = float(hours[-1] - hours[0])
    if span >= MIN_SPAN_H:
        if span <= MAX_SLOW_SPAN_H:
            out.append(("slow", np.arange(k)))
        else:
            for a, b in ((hours[0], hours[0] + 0.6 * span), (hours[-1] - 0.6 * span, hours[-1])):
                out.append(("slow", np.flatnonzero((hours >= a) & (hours <= b))))
    start = float(hours[0])
    while start < hours[-1] - 0.5 * MID_WINDOW_H:
        sel = np.flatnonzero((hours >= start) & (hours <= start + MID_WINDOW_H))
        start += MID_STEP_H
        out.append(("mid", sel))
    return [(tier, sel) for tier, sel in out if sel.size >= MIN_VISITS and hours[sel[-1]] - hours[sel[0]] >= MIN_SPAN_H]


def search(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, sky: StaticSky | None = None,
           exclude: list[Track] | None = None, kind: str = "sun", budget_s: float = 300.0, max_tracks: int = 20,
           avoid: list[np.ndarray] | None = None) -> dict:
    """Faint and slow moving objects of a field.

    Takes what ``movers.search`` takes; ``sky`` is the static sky of
    ``movers.static_sky`` when it exists already, ``exclude`` the tracks the
    linker found (they are not reported again). ``budget_s`` bounds the time:
    the whole-pass search of slow motions always runs, the windows for the
    faster ones only while time is left (``stats["skipped_windows"]``).
    ``avoid``: places of moving objects in every frame (see ``positions``),
    whose light must not be taken for something new.
    """
    t0 = time.time()
    detectors = np.array([int(f.get("detector") or 0) for f in frames])
    if sky is None:
        sky = movers.static_sky(cube, detectors)
    mjd = np.array([float(f["mjd"]) for f in frames])
    ra_c, dec_c = (float(v) for v in wcs.pixel_to_world_values((cube.shape[2] - 1) / 2.0, (cube.shape[1] - 1) / 2.0))
    stats = {"frames": len(frames), "template": kind, "windows": [], "skipped_windows": 0, "passes": 0, "epochs": 0,
             "dropped_frames": 0, "rejected": {}, "candidates": 0, "own_sky": 0, "strongest": []}
    noise_ref = 0.02  # MJy/sr: only sets the scale of the numbers
    tracks: list[Track] = []
    epochs = epochs_of(mjd)
    stats["epochs"] = len(epochs)
    # where the moving objects known so far are in each frame (pixels; NaN: not there)
    taken: list[np.ndarray] = list(avoid or []) + [positions(t, frames, wcs, mjd) for t in exclude or []]
    for own in epochs:
        groups = visits_of(mjd[own])
        groups = [own[g] for g in groups]
        centre = np.array([mjd[g].mean() for g in groups])
        todo = [p for p in _passes(centre) if p.size >= MIN_VISITS and (centre[p[-1]] - centre[p[0]]) * 24.0 >= MIN_SPAN_H]
        if not todo:
            continue
        own_sky = sky_without(cube, detectors, own, sky) if len(epochs) > 1 else sky
        stats["own_sky"] += own_sky is not sky
        moat = moat_of(own_sky)
        stats["clear_share"] = min(round(1.0 - float((moat | own_sky.mask[0]).mean()), 3), stats.get("clear_share", 1.0))
        for p in todo:
            maps = pass_maps(cube, frames, own_sky, [groups[i] for i in p], kind, noise_ref)
            if maps is None:
                continue
            stats["passes"] += 1
            stats["dropped_frames"] += maps.dropped
            found: list[Candidate] = []
            for tier, sel in _windows(maps.hours):
                if tier != "slow" and time.time() - t0 > budget_s:
                    stats["skipped_windows"] += 1
                    continue
                lo, hi = (0.0, SLOW_RATE) if tier == "slow" else (SLOW_RATE * 0.9, MID_RATE)
                c, info = search_window(maps, sel, lo, hi, scale_arcsec, tier, centre=(ra_c, dec_c))
                found += unique(c)  # within a window; paths of different windows are compared after they were judged
                stats["windows"].append(info)
            cands = sorted(found, key=lambda c: -c.snr)
            stats["candidates"] += len(cands)
            kept: list[tuple[Candidate, dict]] = []
            refused: list[Candidate] = []  # paths no orbit allows: not to be found again in a shorter window, which cannot tell
            for c in cands:
                if any(same_path(c, o) for o, _ in kept):
                    continue
                if any(same_path(c, o) for o in refused):
                    stats["rejected"]["unbound"] = stats["rejected"].get("unbound", 0) + 1
                    continue
                if _known(c, exclude or [], wcs, scale_arcsec):
                    stats["rejected"]["known"] = stats["rejected"].get("known", 0) + 1
                    continue
                polish(c, cube, frames, own_sky, mjd, kind)
                ev, why = vet(c, cube, frames, own_sky, mjd, kind, maps, scale_arcsec, taken, moat)
                if ev is not None:
                    # the distance, and with it the final path; the path is judged again as it now is
                    first, path = ev, (c.x, c.y, c.vx, c.vy, c.gamma)
                    distance = (_distance_bright(c, cube, frames, own_sky, ev["rows"])
                                or distance_from_frames(c, cube, frames, own_sky, mjd, kind))
                    ev, why = vet(c, cube, frames, own_sky, mjd, kind, maps, scale_arcsec, taken, moat)
                    if ev is None:  # the path as it was passed; the distance stays what the frames said
                        c.x, c.y, c.vx, c.vy, c.gamma = path
                        ev, why = first, ""
                    if not possible(c, distance, scale_arcsec):
                        ev, why = None, "unbound"
                        refused.append(c)
                        xs, ys = c.at(mjd)  # and its light is not to feed another path
                        inside = (mjd >= c.first_mjd - 0.05) & (mjd <= c.last_mjd + 0.05)
                        taken.append(np.column_stack([np.where(inside, xs, np.nan), np.where(inside, ys, np.nan)]))
                    else:
                        ev["distance"] = distance
                        ev["search_snr"] = round(float(c.snr), 2)
                if len(stats["strongest"]) < 40 and c.snr >= 9.0:
                    stats["strongest"].append({"snr": round(c.snr, 1), "x": round(float(c.x)), "y": round(float(c.y)), "tier": c.tier,
                                               "rate": round(math.hypot(c.vx, c.vy) * scale_arcsec, 2), "result": why or "kept"})
                if ev is None:
                    stats["rejected"][why] = stats["rejected"].get(why, 0) + 1
                    continue
                kept.append((c, ev))
                xs, ys = c.at(mjd)
                inside = (mjd >= c.first_mjd - 0.05) & (mjd <= c.last_mjd + 0.05)
                taken.append(np.column_stack([np.where(inside, xs, np.nan), np.where(inside, ys, np.nan)]))
            for c, ev in sorted(kept, key=lambda k: -k[1]["snr"])[: max(0, max_tracks - len(tracks))]:
                tracks.append(_track(c, ev, frames, wcs, scale_arcsec, kind))
            del maps
    tracks.sort(key=lambda t: -t.extra["stack_snr"])
    stats.update(tracks=len(tracks), seconds=round(time.time() - t0, 1))
    return {"tracks": tracks, "stats": stats, "version": VERSION}


def chance_tracks(cube: np.ndarray, frames: list[dict], wcs, scale_arcsec: float, sky: StaticSky | None = None, trials: int = 3,
                  seed: int = 1, real: list[Track] | None = None, visits: bool = True, strong: bool = True, **kw) -> float:
    """Tracks per search when the times are shuffled within each pass (those
    of the visits, or of the single exposures): what chance gives in this
    field. Counted are the tracks that would be shown without a catalogue
    name (``strong``), or all. ``real``: the objects found with the true
    times (and by the linker); their light is still in the frames after the
    shuffle, scattered along their paths, and is left out."""
    if sky is None:
        sky = movers.static_sky(cube, [int(f.get("detector") or 0) for f in frames])
    avoid = [positions(t, frames, wcs) for t in real or []]
    found = 0
    for k in range(trials):
        out = search(cube, shuffled(frames, seed + k, visits), wcs, scale_arcsec, sky=sky, avoid=avoid, **kw)
        found += sum(1 for t in out["tracks"] if t.extra["stack_strong"] or not strong)
    return found / trials
