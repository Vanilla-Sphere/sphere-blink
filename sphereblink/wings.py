"""Brightness of a star whose core saturates the detector, from its PSF wings.

Stars brighter than about K = 6 fill the wells of the central pixels: the
pipeline flags them (OVERFLOW, NONLINEAR) and an aperture on the core measures
nothing. A few pixels further out the same star is still there, hundreds of
times fainter and well inside the linear range. The surface brightness of
that halo, in a ring around the star, is proportional to the star's flux.

The factor between the two is measured, not assumed (``scripts/calibrate_wings.py``,
81 isolated 2MASS stars of K = 3-11, 29 000 exposures):

1. Unsaturated stars have both an aperture flux and ring levels in every
   exposure. Flux / level of the innermost ring, fitted against wavelength,
   is ``CALIBRATION["ring0"]``; the outer rings follow from their level
   relative to the innermost one, which bright stars give to about 1 %.
2. Above roughly 1 Jy the halo grows faster than the star: compared with
   2MASS J, H and Ks the plain factor reads 1.4 times high at 5 Jy and 1.9
   times high at 20 Jy, the same in all three rings. The cause is not
   established (charge from the saturated core reaching the halo would do
   it). ``_excess`` is the fitted trend; it is divided out.

After both steps the wing fluxes of the calibrators agree with 2MASS to
0.90-1.06 in the median with 11-25 % scatter from star to star, for 0.15 to
20 Jy. That scatter is the systematic error given with every wing flux;
beyond 40 Jy the trend is an extrapolation and the error says so.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import config

# Rings around the star, in native pixels (6.15 arcsec). The innermost ring
# that is free of saturated pixels is used: it has the most signal.
RINGS = ((4.0, 6.0), (6.0, 9.0), (9.0, 13.0))
SKY = (22.0, 30.0)
MIN_GOOD = 0.8  # of a ring's pixels must be usable
SATURATION_FLAGS = ("OVERFLOW", "NONLINEAR")


@lru_cache(maxsize=16)
def _radius(npix: int, scale_arcsec: float) -> np.ndarray:
    c = (npix - 1) / 2.0
    yy, xx = np.mgrid[0:npix, 0:npix]
    return (np.hypot(xx - c, yy - c) * (scale_arcsec / config.NATIVE_PIXEL_ARCSEC)).astype(np.float32)


def ring_levels(image: np.ndarray, scale_arcsec: float, saturated: bool = False) -> list[tuple[float, float, float]]:
    """Median surface brightness above the local sky [MJy/sr] in each ring
    around the image centre, its standard error and the usable fraction of the
    ring. NaN where a ring cannot be measured."""
    r = _radius(image.shape[0], float(scale_arcsec))
    img = image.astype(np.float32, copy=False)
    ok = np.isfinite(img)
    if saturated and ok.any():
        # Saturated cores are painted at the frame's brightest level for
        # display; those pixels are not measurements.
        ok &= img < 0.9 * float(np.nanmax(img))
    edge = float(r[r.shape[0] // 2, 0])  # largest radius that fits the frame
    s0, s1 = min(SKY[0], 0.72 * edge), min(SKY[1], edge)
    sky_px = img[ok & (r >= s0) & (r <= s1)]
    out = []
    if sky_px.size < 50 or s0 < RINGS[0][1] + 4:
        return [(float("nan"), float("nan"), 0.0)] * len(RINGS)
    sky = float(np.median(sky_px))
    sigma = 1.4826 * float(np.median(np.abs(sky_px - sky)))
    for r0, r1 in RINGS:
        ring = (r >= r0) & (r < r1)
        good = ring & ok
        frac = float(good.sum()) / max(1, int(ring.sum()))
        if frac < MIN_GOOD:
            out.append((float("nan"), float("nan"), frac))
            continue
        # pixels of a resampled frame are correlated: count native pixels
        n_native = good.sum() * (scale_arcsec / config.NATIVE_PIXEL_ARCSEC) ** 2
        err = 1.2533 * sigma / np.sqrt(max(1.0, n_native))
        out.append((float(np.median(img[good])) - sky, float(err), frac))
    return out


# Flux [uJy] per ring level [MJy/sr] against wavelength, and the level a ring
# shows around a star of no flux. Written by scripts/calibrate_wings.py fit.
CALIBRATION = {
    "wave_um": [0.7858, 0.886, 0.999, 1.1264, 1.27, 1.432, 1.6146, 1.8205, 2.0527, 2.3145, 2.6097, 2.9425, 3.3177, 3.7408, 4.2179, 4.7558],
    "ring0": [4439762, 5430097, 6285361, 6921698, 7290209, 7382394, 7225524, 6871341, 6382615, 5821378, 5240927, 4681990, 4172324, 3728513, 3358835, 3066351],
    "ring1": [11986576, 15236314, 17246573, 19593188, 20209912, 20617496, 21220945, 21752485, 20535507, 19636795, 20308796, 17796412, 16132929, 14313059, 12753449, 11280924],
    "ring2": [33081313, 40792228, 44481411, 52820252, 52414346, 52411270, 56725190, 61424079, 58596070, 56657444, 65268448, 57654073, 51468871, 46966655, 41740513, 36756304],
    "offset": [-0.000386, 0.00341, 0.00175],
}
_LOG_WAVE = np.log(CALIBRATION["wave_um"])
APERTURE_SCALE = 0.97  # unsaturated aperture fluxes / 2MASS, so both kinds of point share a scale
REFERENCE_R = 41.0  # resolving power of the detectors the trend was measured on
EXTRAPOLATED_JY = 40.0


def _excess(x_jy: float) -> float:
    """How much too high the plain factor reads for a star whose plain wing
    flux is ``x_jy`` (at the photon rate of an R = 41 channel)."""
    return max(0.86, 0.931 + 0.763 * float(np.log10(max(x_jy, 1e-3))))


def measure(
    image: np.ndarray, scale_arcsec: float, wave_um: float, bandwidth_um: float = float("nan"), saturated: bool = True
) -> dict | None:
    """Flux of the star at the image centre from its halo, or None when no
    ring is usable or the halo is not detected."""
    if not np.isfinite(wave_um):
        return None
    for ring, (level, err, _frac) in enumerate(ring_levels(image, scale_arcsec, saturated)):
        if np.isfinite(level):
            break
    else:
        return None
    level -= CALIBRATION["offset"][ring]
    if not level > 3.0 * err:
        return None
    factor = float(np.exp(np.interp(np.log(wave_um), _LOG_WAVE, np.log(CALIBRATION[f"ring{ring}"]))))
    plain = factor * level
    # The trend follows the photon rate: a narrower channel sees less of it.
    r_power = wave_um / bandwidth_um if np.isfinite(bandwidth_um) and bandwidth_um > 0 else REFERENCE_R
    x = plain / 1e6 * REFERENCE_R / max(r_power, 1.0)
    scale = APERTURE_SCALE / _excess(x)
    flux = plain * scale
    extrapolated = x > EXTRAPOLATED_JY
    systematic = 0.5 if extrapolated else 0.25 if x > 10 else 0.2
    return {
        "flux_ujy": flux,
        "flux_err_ujy": float(np.hypot(factor * err * scale, systematic * flux)),
        "ring": ring,
        "extrapolated": extrapolated,
    }
