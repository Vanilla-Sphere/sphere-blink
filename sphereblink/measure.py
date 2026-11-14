"""Measure where an object is in every SPHEREx exposure, and fit its motion.

The viewer shows resampled pictures; a measurement wants the pixels the
detector recorded. For every exposure that covers the object this module
reads a small native stamp (a few image rows, by HTTP range request, like
``cutout.py``), then:

  1. predicts where the object and nearby Gaia stars fall on the detector on
     that date (proper motion and parallax applied);
  2. fits a pixel-integrated Gaussian PSF to each of them;
  3. uses the Gaia stars to remove the pointing error of that exposure
     (their measured minus predicted position is the same for the whole stamp);
  4. turns the object's detector position into ICRS with the exposure's WCS.

Objects too faint for one exposure are added up along their predicted path,
one stack per sky pass, and measured on the stack. Finally a straight-line
motion (plus parallax) is fitted to all positions.

Nothing here knows about HTTP or the database: ``measure_target`` returns a
plain dictionary. See ``measure_api.py`` for the job runner.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field

import httpx
import numpy as np
from astropy.wcs import WCS
from scipy.ndimage import label, map_coordinates
from scipy.optimize import least_squares
from scipy.special import erf

from . import config
from .cutout import (
    BLOCK,
    DET_SIZE,
    FIRST_FETCH,
    FLAGS_FETCH,
    ROW_BYTES,
    SATURATION_FLAGS,
    _bits,
    _FLAG_CARDS,
    _get,
    _header_end,
    _parse_header,
    _rice_rows,
    _WCS_CARDS,
    flag_planes,
    wavelength_at,
)
from .sia import Exposure

ARCSEC2_TO_SR = (math.pi / 180.0 / 3600.0) ** 2
TO_UJY = (config.NATIVE_PIXEL_ARCSEC**2) * ARCSEC2_TO_SR * 1e12  # MJy/sr summed over pixels -> uJy
PIX = config.NATIVE_PIXEL_ARCSEC
MJD_J2000 = 51544.5

HALF_ROWS = 24  # stamp half height in detector pixels (2.5 arcmin)
HALF_COLS = 40  # stamp half width (4 arcmin); the rows are downloaded whole anyway
BOX = 3  # the PSF is fitted in a (2*BOX+1)^2 pixel window
WING_BOX = 12  # window for a star whose core is saturated
MAX_SHIFT = 1.5  # how far [pixels] from the prediction the object may be found
MIN_SNR = 6.0  # a single-exposure position is kept above this
STACK_MIN_SNR = 5.0
STACK_HALF = 10  # stacks are (2*10+1)^2 pixels of half the native size: +-31 arcsec
REF_MIN_SNR = 15.0
REF_MAX = 4
NEIGHBOUR_ARCSEC = 30.0  # catalogue stars this close are fitted together with the object
PASS_GAP_DAYS = 45.0  # exposures closer than this belong to one sky pass
ERR_FLOOR_ARCSEC = 0.15  # no single position is trusted better than this
PARALLAX_FLOOR_ARCSEC = 0.15
# Brightness of one pass against another is not trusted better than this
# (measured on stars that do not vary): PSF fluxes 2 %, ring fluxes of saturated stars 12 %.
FLUX_FLOOR = {"psf": 0.02, "wings": 0.12}


def year_of(mjd: float) -> float:
    """Julian year of an MJD."""
    return 2000.0 + (mjd - MJD_J2000) / 365.25


# ----------------------------------------------------------------------------- where a star should be
def earth_au(mjd) -> np.ndarray:
    """Barycentric position of the Earth [au], ICRS axes. SPHEREx is in low
    Earth orbit, 5e-5 au from the geocentre: irrelevant for parallax."""
    from astropy.coordinates import get_body_barycentric
    from astropy.time import Time

    p = get_body_barycentric("earth", Time(np.atleast_1d(mjd), format="mjd", scale="tdb"), ephemeris="builtin")
    return np.stack([p.x.to_value("au"), p.y.to_value("au"), p.z.to_value("au")], axis=-1)


def parallax_factors(ra: float, dec: float, mjd) -> tuple[np.ndarray, np.ndarray]:
    """Displacement east and north, per unit of parallax, at each date."""
    e = earth_au(mjd)
    a, d = math.radians(ra), math.radians(dec)
    pe = e[:, 0] * math.sin(a) - e[:, 1] * math.cos(a)
    pn = e[:, 0] * math.cos(a) * math.sin(d) + e[:, 1] * math.sin(a) * math.sin(d) - e[:, 2] * math.cos(d)
    return pe, pn


@dataclass
class Body:
    """A catalogue object: position at ``epoch`` (Julian year), proper motion
    in mas/yr (pmra includes cos dec), parallax in mas."""

    name: str
    ra: float
    dec: float
    epoch: float = 2016.0
    pmra: float = 0.0
    pmdec: float = 0.0
    parallax: float = 0.0
    mag: float | None = None
    catalog: str = ""
    catalog_id: str = ""

    def offset_at(self, mjd) -> tuple[np.ndarray, np.ndarray]:
        """Offset east, north [arcsec] from (ra, dec) at each date."""
        mjd = np.atleast_1d(np.asarray(mjd, dtype=float))
        dt = year_of(mjd) - self.epoch
        east, north = self.pmra / 1000.0 * dt, self.pmdec / 1000.0 * dt
        if self.parallax:
            pe, pn = parallax_factors(self.ra, self.dec, mjd)
            # the catalogue position is barycentric: no parallax term at the epoch itself
            east, north = east + self.parallax / 1000.0 * pe, north + self.parallax / 1000.0 * pn
        return east, north

    def at(self, mjd: float) -> tuple[float, float]:
        east, north = self.offset_at(mjd)
        return shift(self.ra, self.dec, float(east[0]), float(north[0]))


def shift(ra: float, dec: float, east_arcsec: float, north_arcsec: float) -> tuple[float, float]:
    """Move a position by a small tangent-plane offset."""
    d = dec + north_arcsec / 3600.0
    r = ra + east_arcsec / 3600.0 / max(math.cos(math.radians(dec)), 1e-6)
    return r % 360.0, max(-90.0, min(90.0, d))


def offset(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    """Tangent-plane offset east, north [arcsec] of (ra, dec) from (ra0, dec0)."""
    dra = (ra - ra0 + 180.0) % 360.0 - 180.0
    return dra * 3600.0 * math.cos(math.radians(dec0)), (dec - dec0) * 3600.0


# ----------------------------------------------------------------------------- native stamps
@dataclass
class Stamp:
    exposure: Exposure
    wcs: WCS
    x0: int  # detector column / row of the stamp's first pixel
    y0: int
    data: np.ndarray  # MJy/sr, sky-subtracted, NaN = unusable
    saturated: np.ndarray  # bool, cores of bright stars
    flags: np.ndarray
    planes: dict[str, int]
    sigma: float  # sky noise per pixel, MJy/sr

    def pixel(self, ra: float, dec: float) -> tuple[float, float]:
        x, y = self.wcs.world_to_pixel_values(ra, dec)
        return float(x) - self.x0, float(y) - self.y0

    def world(self, x: float, y: float) -> tuple[float, float]:
        ra, dec = self.wcs.pixel_to_world_values(x + self.x0, y + self.y0)
        return float(ra), float(dec)

    def flag_names(self, x: float, y: float, r: float = 1.5) -> list[str]:
        h, w = self.flags.shape
        yy, xx = np.mgrid[0:h, 0:w]
        sel = (xx - x) ** 2 + (yy - y) ** 2 <= r * r
        v = int(np.bitwise_or.reduce(self.flags[sel].ravel())) if sel.any() else 0
        return sorted(n for n, bit in self.planes.items() if v & bit)


async def read_stamp(client: httpx.AsyncClient, exposure: Exposure, ra: float, dec: float,
                     half_rows: int = HALF_ROWS, half_cols: int = HALF_COLS) -> Stamp | None:
    """Native pixels around (ra, dec); None when the position is off the detector."""
    url = exposure.http_url
    head = await _get(client, url, 0, FIRST_FETCH)
    p_end = _header_end(head, 0)
    i_end = _header_end(head, p_end) if p_end is not None else None
    while i_end is None:
        if len(head) > 60 * BLOCK:
            raise ValueError("IMAGE header not found")
        head += await _get(client, url, len(head), len(head) + FIRST_FETCH)
        p_end = _header_end(head, 0)
        i_end = _header_end(head, p_end) if p_end is not None else None
    hdr = _parse_header(head, p_end, i_end, _WCS_CARDS)
    if hdr.get("NAXIS1") != DET_SIZE or hdr.get("NAXIS2") != DET_SIZE or hdr.get("BITPIX") != -32:
        raise ValueError(f"unexpected IMAGE layout in {exposure.uid}")
    wcs = WCS(hdr)
    tx, ty = (float(v) for v in wcs.world_to_pixel_values(ra, dec))
    edge = BOX + 2
    if not (edge <= tx <= DET_SIZE - 1 - edge and edge <= ty <= DET_SIZE - 1 - edge):
        return None
    x0, x1 = max(int(round(tx)) - half_cols, 0), min(int(round(tx)) + half_cols + 1, DET_SIZE)
    y0, y1 = max(int(round(ty)) - half_rows, 0), min(int(round(ty)) + half_rows + 1, DET_SIZE)

    flags_hdr_off = i_end + (-(-DET_SIZE * ROW_BYTES // BLOCK)) * BLOCK
    img_bytes, fl = await asyncio.gather(
        _get(client, url, i_end + y0 * ROW_BYTES, i_end + y1 * ROW_BYTES),
        _get(client, url, flags_hdr_off, flags_hdr_off + FLAGS_FETCH),
    )
    sci = np.frombuffer(img_bytes, dtype=">f4").reshape(y1 - y0, DET_SIZE)[:, x0:x1].astype(np.float64)

    f_end = _header_end(fl, 0)
    while f_end is None or len(fl) < f_end + 8 * DET_SIZE:
        if len(fl) > 60 * BLOCK:
            raise ValueError("FLAGS header not found")
        fl += await _get(client, url, flags_hdr_off + len(fl), flags_hdr_off + len(fl) + FLAGS_FETCH)
        f_end = _header_end(fl, 0)
    fhdr = _parse_header(fl, 0, f_end, _FLAG_CARDS)
    if fhdr.get("ZCMPTYPE") != "RICE_1" or fhdr.get("ZTILE1") != DET_SIZE or fhdr.get("NAXIS1") != 8:
        raise ValueError(f"unexpected FLAGS layout in {exposure.uid}")
    blocksize = 32
    for k in range(1, 4):
        if str(fhdr.get(f"ZNAME{k}", "")).strip() == "BLOCKSIZE":
            blocksize = int(fhdr[f"ZVAL{k}"])
    index = np.frombuffer(fl[f_end : f_end + 8 * DET_SIZE], dtype=">i4").reshape(DET_SIZE, 2)
    lens, offs = index[y0:y1, 0].astype(np.int64), index[y0:y1, 1].astype(np.int64)
    heap_off = flags_hdr_off + f_end + int(fhdr.get("THEAP", 8 * DET_SIZE))
    lo, hi = int(offs.min()), int((offs + lens).max())
    heap = await _get(client, url, heap_off + lo, heap_off + hi)
    flags = _rice_rows(heap, lens, offs, lo, blocksize)[:, x0:x1]
    planes = flag_planes(fhdr)
    return make_stamp(exposure, wcs, x0, y0, sci, flags, planes)


def make_stamp(exposure, wcs, x0, y0, sci, flags, planes) -> Stamp:
    bad = (flags & _bits(planes, config.BAD_FLAG_NAMES)) != 0
    saturated = (flags & _bits(planes, SATURATION_FLAGS)) != 0
    if saturated.any():  # lone overflow pixels are cosmic-ray hits, not star cores
        lab, n_lab = label(saturated)
        sizes = np.bincount(lab.ravel(), minlength=n_lab + 1)
        saturated = (sizes >= 3)[lab] & (lab > 0)
    data = np.array(sci, dtype=np.float64)
    data[bad | ~np.isfinite(data)] = np.nan
    ok = np.isfinite(data)
    sky, sigma = 0.0, float("nan")
    if ok.sum() >= 30:
        v = data[ok]
        for _ in range(4):  # clipped median: the sky level without the stars
            sky = float(np.median(v))
            sigma = 1.4826 * float(np.median(np.abs(v - sky)))
            keep = np.abs(v - sky) < 3.0 * max(sigma, 1e-12)
            if keep.sum() < 30 or keep.all():
                break
            v = v[keep]
    return Stamp(exposure, wcs, x0, y0, data - sky, saturated, flags, planes, sigma)


# ----------------------------------------------------------------------------- PSF fit
def _gauss_pix(x, y, x0, y0, s):
    """Fraction of a unit circular Gaussian that falls in the pixel centred on (x, y)."""
    k = 1.0 / (math.sqrt(2.0) * s)
    gx = 0.5 * (erf((x + 0.5 - x0) * k) - erf((x - 0.5 - x0) * k))
    gy = 0.5 * (erf((y + 0.5 - y0) * k) - erf((y - 0.5 - y0) * k))
    return gx * gy


@dataclass
class Fit:
    x: float  # pixel coordinates of the array that was fitted
    y: float
    ex: float  # 1 sigma, pixels
    ey: float
    flux: float  # image units * pixel
    eflux: float
    width: float  # Gaussian sigma (or Moffat core radius), pixels
    snr: float
    npix: int
    masked: int  # unusable pixels in the window
    core_masked: int  # unusable pixels among the 3 x 3 around the centre
    kind: str = "psf"  # psf | wings

    @property
    def ok(self) -> bool:
        need = [self.x, self.y, self.ex, self.ey] + ([self.flux, self.eflux] if self.kind == "psf" else [])
        return bool(np.isfinite(need).all())


def _window(data, x, y, box):
    h, w = data.shape
    cx, cy = int(round(x)), int(round(y))
    if not (box <= cx < w - box and box <= cy < h - box):
        return None
    win = data[cy - box : cy + box + 1, cx - box : cx + box + 1]
    yy, xx = np.mgrid[cy - box : cy + box + 1, cx - box : cx + box + 1].astype(float)
    return win, xx, yy


def _core_masked(data, x, y) -> int:
    h, w = data.shape
    cx, cy = int(round(min(max(x, 1), w - 2))), int(round(min(max(y, 1), h - 2)))
    return int((~np.isfinite(data[cy - 1 : cy + 2, cx - 1 : cx + 2])).sum())


def fit_psf(data: np.ndarray, x: float, y: float, noise: float, box: int = BOX, max_shift: float = MAX_SHIFT,
            neighbours: list[tuple] | None = None, width_bounds: tuple[float, float] = (0.3, 2.5)) -> Fit | None:
    """Least-squares fit of a pixel-integrated Gaussian plus a constant sky in
    a small window around (x, y).

    ``neighbours`` are known stars, (x, y) or (x, y, flux); those inside the
    window are fitted at the same time with their position fixed, so that a
    brighter star next to the object does not pull the fit towards itself.
    A neighbour given with its flux is held at that flux, which keeps the fit
    well determined when the two are closer than the PSF is wide. Masked
    pixels (NaN) are left out. Returns None when there is nothing to fit.
    """
    got = _window(data, x, y, box)
    if got is None or not np.isfinite(noise) or noise <= 0:
        return None
    win, xx, yy = got
    ok = np.isfinite(win)
    n = int(ok.sum())
    if n < 0.55 * win.size:
        return None
    v, px, py = win[ok], xx[ok], yy[ok]
    inside = [tuple(nb) for nb in (neighbours or [])
              if abs(nb[0] - x) <= box + 1 and abs(nb[1] - y) <= box + 1 and math.hypot(nb[0] - x, nb[1] - y) > 0.5]
    fixed = [nb for nb in inside if len(nb) > 2 and nb[2] is not None]
    near = [nb[:2] for nb in inside if len(nb) < 3 or nb[2] is None][:4]
    sky0 = float(np.median(v))
    f0 = max(float(np.sum(v - sky0)), 3.0 * noise)

    def model(p):
        m = p[2] * _gauss_pix(px, py, p[0], p[1], p[4]) + p[3]
        for k, (nx, ny) in enumerate(near):
            m = m + p[5 + k] * _gauss_pix(px, py, nx, ny, p[4])
        for nx, ny, nf in fixed:
            m = m + nf * _gauss_pix(px, py, nx, ny, p[4])
        return m

    p0 = [x, y, f0, sky0, min(max(0.75, width_bounds[0] * 1.2), width_bounds[1] * 0.8)] + [f0 * 0.5] * len(near)
    lo = [x - max_shift, y - max_shift, -np.inf, -np.inf, width_bounds[0]] + [0.0] * len(near)
    hi = [x + max_shift, y + max_shift, np.inf, np.inf, width_bounds[1]] + [np.inf] * len(near)
    if not inside:
        # start from the brightest pixel near the prediction when it is clearly a star
        peak = np.where(ok & ((xx - x) ** 2 + (yy - y) ** 2 <= max_shift**2), win, -np.inf)
        if np.isfinite(peak).any() and peak.max() - sky0 > 5.0 * noise:
            j = np.unravel_index(int(np.argmax(peak)), peak.shape)
            p0[0] = float(np.clip(xx[j], lo[0] + 1e-3, hi[0] - 1e-3))
            p0[1] = float(np.clip(yy[j], lo[1] + 1e-3, hi[1] - 1e-3))
    scale = [1, 1, max(f0, noise), noise, 0.5] + [max(f0, noise)] * len(near)
    try:
        res = least_squares(lambda p: (model(p) - v) / noise, p0, bounds=(lo, hi), x_scale=scale)
    except Exception:
        return None
    p = res.x
    chi2 = float(np.sum(res.fun**2)) / max(n - len(p), 1)
    try:
        cov = np.linalg.inv(res.jac.T @ res.jac) * max(chi2, 1.0)
        err = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:
        return None
    flux, eflux = float(p[2]), float(err[2])
    return Fit(x=float(p[0]), y=float(p[1]), ex=float(err[0]), ey=float(err[1]), flux=flux, eflux=eflux,
               width=float(p[4]), snr=flux / eflux if eflux > 0 else 0.0, npix=n, masked=int(win.size - n),
               core_masked=_core_masked(data, p[0], p[1]))


def fit_wings(data: np.ndarray, x: float, y: float, noise: float, box: int = WING_BOX, max_shift: float = 2.5) -> Fit | None:
    """Centre of a star whose core is saturated (masked): a round Moffat
    profile fitted to the wings that are left. Less exact than ``fit_psf``
    (the wings are not perfectly round), so these positions are only used
    when there is nothing better. No flux: see ``ring_flux``."""
    got = _window(data, x, y, box)
    if got is None or not np.isfinite(noise) or noise <= 0:
        return None
    win, xx, yy = got
    ok = np.isfinite(win)
    n = int(ok.sum())
    if n < 0.4 * win.size:
        return None
    v, px, py = win[ok], xx[ok], yy[ok]
    sky0 = float(np.percentile(v, 5))
    a0 = max(float(v.max() - sky0) * 5.0, 10 * noise)

    def model(p):
        return p[2] * (1.0 + ((px - p[0]) ** 2 + (py - p[1]) ** 2) / p[3] ** 2) ** (-p[4]) + p[5]

    def resid(p):
        m = model(p)
        return (m - v) / np.sqrt(noise**2 + (0.1 * np.maximum(m, 0.0)) ** 2)

    p0 = [x, y, a0, 2.0, 1.5, sky0]
    lo = [x - max_shift, y - max_shift, 0.0, 0.3, 0.5, -np.inf]
    hi = [x + max_shift, y + max_shift, np.inf, 12.0, 6.0, np.inf]
    try:
        res = least_squares(resid, p0, bounds=(lo, hi), x_scale=[1, 1, a0, 1, 1, max(noise, 1e-9)], loss="soft_l1", max_nfev=60)
    except Exception:
        return None
    p = res.x
    chi2 = float(np.sum(res.fun**2)) / max(n - 6, 1)
    try:
        err = np.sqrt(np.clip(np.diag(np.linalg.inv(res.jac.T @ res.jac) * max(chi2, 1.0)), 0, None))
    except np.linalg.LinAlgError:
        return None
    # how far the masked core reaches, and how strongly the wings stand out just outside it
    r_all = np.hypot(xx - p[0], yy - p[1])
    lab, _ = label(~ok)
    core = lab[int(np.argmin(np.where(~ok, r_all, np.inf)) // win.shape[1]), int(np.argmin(np.where(~ok, r_all, np.inf)) % win.shape[1])] if (~ok).any() else 0
    r_mask = float(r_all[lab == core].max()) if core else 0.0
    r = np.hypot(px - p[0], py - p[1])
    edge = (r > r_mask) & (r <= r_mask + 2.0)
    snr = float(np.sum(v[edge] - p[5]) / (noise * math.sqrt(edge.sum()))) if edge.sum() >= 5 else 0.0
    return Fit(x=float(p[0]), y=float(p[1]), ex=float(err[0]), ey=float(err[1]), flux=float("nan"), eflux=float("nan"),
               width=r_mask, snr=snr, npix=n, masked=int(win.size - n), core_masked=_core_masked(data, p[0], p[1]),
               kind="wings")


def ring_flux(data: np.ndarray, x: float, y: float, noise: float, r_in: float, r_out: float) -> tuple[float, float]:
    """Light in the ring r_in..r_out [pixels] around a saturated star, in
    image units * pixel. Not the star's total flux, but proportional to it
    as long as the same ring is used every time: enough for a light curve."""
    h, w = data.shape
    box = int(math.ceil(r_out)) + 1
    cx, cy = int(round(x)), int(round(y))
    if not (box <= cx < w - box and box <= cy < h - box):
        return float("nan"), float("nan")
    win = data[cy - box : cy + box + 1, cx - box : cx + box + 1]
    yy, xx = np.mgrid[cy - box : cy + box + 1, cx - box : cx + box + 1].astype(float)
    r = np.hypot(xx - x, yy - y)
    ring = (r >= r_in) & (r <= r_out)
    good = ring & np.isfinite(win)
    if good.sum() < 0.8 * ring.sum():
        return float("nan"), float("nan")
    flux = float(np.sum(win[good])) * ring.sum() / good.sum()
    return flux, float(max(noise * math.sqrt(ring.sum()), 0.01 * abs(flux)))


# ----------------------------------------------------------------------------- one exposure
@dataclass
class Detection:
    obs_id: str
    detector: int
    collection: str
    mjd: float
    ra: float
    dec: float
    pos_err_arcsec: float
    east: float  # measured minus catalogue prediction, arcsec
    north: float
    wave_um: float | None
    flux_ujy: float | None
    flux_err_ujy: float | None
    snr: float
    flags: list[str] = field(default_factory=list)
    method: str = "psf"  # psf | wings | stack
    n_exposures: int = 1
    used: bool = True  # False = left out of the track fit
    rel_flux: float | None = None  # flux / the object's mean spectrum at this wavelength
    x: float = float("nan")  # where it was found in its stamp, pixels (not stored)
    y: float = float("nan")
    mask_radius: float = 0.0  # saturated stars: how far the masked core reaches, pixels

    def to_dict(self) -> dict:
        return {
            "obs_id": self.obs_id, "detector": self.detector, "collection": self.collection, "mjd": _r(self.mjd, 6),
            "ra": _r(self.ra, 7), "dec": _r(self.dec, 7), "pos_err_arcsec": _r(self.pos_err_arcsec, 3),
            "east": _r(self.east, 3), "north": _r(self.north, 3), "wave_um": _r(self.wave_um, 4),
            "flux_ujy": _r(self.flux_ujy, 2), "flux_err_ujy": _r(self.flux_err_ujy, 2), "snr": _r(self.snr, 1),
            "flags": self.flags, "method": self.method, "n_exposures": self.n_exposures, "used": self.used,
            "rel_flux": _r(self.rel_flux, 4),
        }


def _r(v, n):
    return None if v is None or not np.isfinite(v) else round(float(v), n)


def _neighbour_pixels(stamp: Stamp, neighbours: list[Body], mjd: float) -> list[tuple[float, float]]:
    return [stamp.pixel(*b.at(mjd)) for b in neighbours]


def measure_stamp(stamp: Stamp, target: Body, neighbours: list[Body] | None = None, wmap=None,
                  max_shift: float = MAX_SHIFT) -> Detection | None:
    """The target's position in one exposure. None when it cannot be fitted;
    whether it counts as a detection (``snr``) is for the caller to decide."""
    mjd = stamp.exposure.mjd
    ra_p, dec_p = target.at(mjd)
    x, y = stamp.pixel(ra_p, dec_p)
    f = fit_psf(stamp.data, x, y, stamp.sigma, max_shift=max_shift, neighbours=_neighbour_pixels(stamp, neighbours or [], mjd))
    if f is None or not f.ok:
        f = fit_wings(stamp.data, x, y, stamp.sigma)
        if f is None or not f.ok:
            return None
    if not good_fit(f, x, y, max_shift if f.kind == "psf" else 2.5):
        return None
    ra, dec = stamp.world(f.x, f.y)
    e, n = offset(ra, dec, ra_p, dec_p)
    wave, _ = wavelength_at(wmap, f.x + stamp.x0, f.y + stamp.y0)
    names = stamp.flag_names(f.x, f.y)
    flux, eflux = f.flux * TO_UJY, f.eflux * TO_UJY
    if f.kind == "wings":
        names = sorted(set(names) | {"SATURATED"})
    elif f.core_masked > 2:
        # the brightest pixels are missing: the position holds, the flux does not
        names = sorted(set(names) | {"SATURATED"})
        flux = eflux = float("nan")
    return Detection(
        obs_id=stamp.exposure.obs_id, detector=stamp.exposure.detector, collection=stamp.exposure.collection, mjd=mjd,
        ra=ra, dec=dec, pos_err_arcsec=float(math.hypot(f.ex, f.ey) * PIX), east=e, north=n,
        wave_um=wave if np.isfinite(wave) else None, flux_ujy=flux, flux_err_ujy=eflux,
        snr=f.snr, flags=names, method=f.kind, x=f.x, y=f.y, mask_radius=f.width if f.kind == "wings" else 0.0,
    )


def good_fit(f: Fit, x: float, y: float, max_shift: float) -> bool:
    """A fit that ran into its limits did not find the object."""
    if abs(f.x - x) > max_shift - 0.02 or abs(f.y - y) > max_shift - 0.02:
        return False
    if f.kind == "psf":
        return 0.31 < f.width < 2.4
    return True


def pointing_check(stamp: Stamp, refs: list[Body], avoid: tuple[float, float] | None = None) -> tuple[float, float, int] | None:
    """Median measured-minus-Gaia offset (east, north [arcsec], number of
    stars) of the reference stars in a stamp: how well the exposure's WCS
    agrees with Gaia. Used as a quality figure, not as a correction: the
    WCS of Level 2 images is already as good as these stars can tell."""
    mjd = stamp.exposure.mjd
    h, w = stamp.data.shape
    cand = []
    for b in refs:
        ra, dec = b.at(mjd)
        x, y = stamp.pixel(ra, dec)
        if not (BOX + 1 <= x < w - BOX - 1 and BOX + 1 <= y < h - BOX - 1):
            continue
        if avoid is not None and math.hypot(x - avoid[0], y - avoid[1]) < 2 * BOX:
            continue
        cand.append((b, ra, dec, x, y))
    offs = []
    for b, ra, dec, x, y in cand:
        if any(o is not b and math.hypot(x - ox, y - oy) < BOX + 1.5 for o, _, _, ox, oy in cand):
            continue  # a reference star must be alone in its window
        f = fit_psf(stamp.data, x, y, stamp.sigma, max_shift=1.5)
        if f is None or not f.ok or f.snr < REF_MIN_SNR or f.masked > 4 or not good_fit(f, x, y, 1.5):
            continue
        ra_m, dec_m = stamp.world(f.x, f.y)
        offs.append(offset(ra_m, dec_m, ra, dec))
        if len(offs) >= REF_MAX:
            break
    if len(offs) < 2:
        return None
    a = np.array(offs)
    return float(np.median(a[:, 0])), float(np.median(a[:, 1])), len(offs)


# ----------------------------------------------------------------------------- stacks for faint objects
def split_passes(mjds, gap: float = PASS_GAP_DAYS) -> np.ndarray:
    """Number each date by the sky pass it belongs to (SPHEREx returns to a
    position about every six months and stays for days to weeks)."""
    mjds = np.asarray(mjds, dtype=float)
    order = np.argsort(mjds)
    out = np.zeros(len(mjds), dtype=int)
    k = 0
    for a, b in zip(order[:-1], order[1:]):
        if mjds[b] - mjds[a] > gap:
            k += 1
        out[b] = k
    return out


def _resample(stamp: Stamp, ra0: float, dec0: float, half: int, scale: float) -> np.ndarray:
    """North-up picture (east to the left) centred on (ra0, dec0)."""
    g = np.arange(-half, half + 1) * scale
    east, north = np.meshgrid(-g, g)
    ra = ra0 + east / 3600.0 / max(math.cos(math.radians(dec0)), 1e-6)
    dec = dec0 + north / 3600.0
    x, y = stamp.wcs.world_to_pixel_values(ra, dec)
    x, y = x - stamp.x0, y - stamp.y0
    ok = np.isfinite(stamp.data)
    v = map_coordinates(np.where(ok, stamp.data, 0.0), [y, x], order=1, mode="constant", cval=0.0)
    w = map_coordinates(ok.astype(float), [y, x], order=1, mode="constant", cval=0.0)
    return np.where(w > 0.8, v / np.maximum(w, 1e-6), np.nan)


def _cube(stamps: list[Stamp], target: Body, half: int, scale: float) -> np.ndarray | None:
    """Every stamp resampled around where the target should be on its own
    date, so a moving object stays in the centre."""
    ims = []
    for st in stamps:
        im = _resample(st, *target.at(st.exposure.mjd), half, scale)
        if np.isfinite(im).mean() < 0.7:
            continue
        ims.append(im - np.nanmedian(im))
    return np.array(ims) if len(ims) >= 3 else None


def _combine(cube: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Clipped mean of a cube and its noise per pixel."""
    import warnings

    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(cube, axis=0)
        sd = 1.4826 * np.nanmedian(np.abs(cube - med), axis=0)
        keep = np.abs(cube - med) <= 3.0 * sd + 1e-12
    cnt = keep.sum(axis=0)
    stack = np.where(cnt > 0, np.sum(np.where(keep, cube, 0.0), axis=0) / np.maximum(cnt, 1), np.nan)
    v = stack[np.isfinite(stack)]
    if v.size < 30:
        return None
    return stack, 1.4826 * float(np.median(np.abs(v - np.median(v))))


def stack_stamps(stamps: list[Stamp], target: Body, half: int = STACK_HALF, scale: float = PIX / 2) -> tuple[np.ndarray, float] | None:
    """Add exposures along the target's predicted path. Returns the clipped
    mean picture (north up, east left, centred on the target) and its noise."""
    cube = _cube(stamps, target, half, scale)
    return None if cube is None else _combine(cube)


STACK_FIT = dict(box=4, max_shift=3.0, width_bounds=(0.7, 3.5))


def _stack_pixels(bodies: list[Body], mjd: float, ra0: float, dec0: float, half: int, scale: float) -> list[tuple[float, float]]:
    out = []
    for b in bodies:
        e, n = offset(*b.at(mjd), ra0, dec0)
        out.append((half - e / scale, half + n / scale))  # the column grows to the west
    return out


def measure_stack(stamps: list[Stamp], target: Body, neighbours: list[Body] | None = None, waves: list[float] | None = None,
                  neighbour_flux: list[float | None] | None = None, half: int = STACK_HALF, scale: float = PIX / 2,
                  boot: int = 16) -> Detection | None:
    """Position of a faint target in the co-added exposures of one detector
    during one sky pass.

    The pixels of the stack are not independent (it is resampled), so the
    errors do not come from the fit: the fit is repeated on stacks of
    exposures drawn at random with replacement, and the scatter of the
    results is the error."""
    cube = _cube(stamps, target, half, scale)
    got = None if cube is None else _combine(cube)
    if got is None:
        return None
    stack, noise = got
    mjd = float(np.mean([s.exposure.mjd for s in stamps]))
    ra_p, dec_p = target.at(mjd)
    near = _stack_pixels(neighbours or [], mjd, ra_p, dec_p, half, scale)
    if neighbour_flux:
        near = [(x, y, f) for (x, y), f in zip(near, neighbour_flux)]
    f = fit_psf(stack, half, half, noise, neighbours=near, **STACK_FIT)
    if f is None or not f.ok or not good_fit(f, half, half, 3.0):
        return None
    xs, ys, fl = [], [], []
    if len(cube) >= 5:
        rng = np.random.default_rng(len(cube) * 1000 + stamps[0].exposure.detector)
        for _ in range(boot):
            g = _combine(cube[rng.integers(0, len(cube), len(cube))])
            fb = None if g is None else fit_psf(g[0], half, half, g[1], neighbours=near, **STACK_FIT)
            if fb is not None and fb.ok:
                xs.append(fb.x), ys.append(fb.y), fl.append(fb.flux)
    if len(fl) >= 8:
        ex, ey, ef = float(np.std(xs)), float(np.std(ys)), float(np.std(fl))
    else:  # too few exposures to resample: the fit's own errors, widened for the correlated pixels
        ex, ey, ef = 3.0 * f.ex, 3.0 * f.ey, 3.0 * f.eflux
    e, n = -(f.x - half) * scale, (f.y - half) * scale
    ra, dec = shift(ra_p, dec_p, e, n)
    to_ujy = TO_UJY * (scale / PIX) ** 2
    first, last = min(s.exposure.mjd for s in stamps), max(s.exposure.mjd for s in stamps)
    return Detection(
        obs_id=f"stack:{first:.2f}-{last:.2f}", detector=stamps[0].exposure.detector, collection=stamps[0].exposure.collection,
        mjd=mjd, ra=ra, dec=dec, pos_err_arcsec=float(math.hypot(ex, ey) * scale), east=e, north=n,
        wave_um=float(np.median(waves)) if waves else None, flux_ujy=f.flux * to_ujy, flux_err_ujy=ef * to_ujy,
        snr=f.flux / ef if ef > 0 else 0.0, flags=[], method="stack", n_exposures=len(cube),
    )


def neighbour_fluxes(groups: list[list[Stamp]], target: Body, neighbours: list[Body], half: int = STACK_HALF,
                     scale: float = PIX / 2, min_sep_arcsec: float = 1.5 * PIX) -> list[float | None]:
    """Flux of each neighbour star in one detector, measured in the sky pass
    where the moving target is farthest from it (None when they are never
    apart). ``groups`` are the stamps of that detector, one list per pass."""
    out: list[float | None] = []
    for b in neighbours:
        best, best_sep = None, min_sep_arcsec
        for grp in groups:
            if len(grp) < 3:
                continue
            mjd = float(np.mean([s.exposure.mjd for s in grp]))
            sep = math.hypot(*offset(*target.at(mjd), *b.at(mjd)))
            if sep > best_sep:
                best, best_sep = grp, sep
        flux = None
        if best is not None:
            got = stack_stamps(best, b, half, scale)  # centred on the star this time
            if got is not None:
                mjd = float(np.mean([s.exposure.mjd for s in best]))
                others = [target] + [o for o in neighbours if o is not b]
                f = fit_psf(got[0], half, half, got[1], neighbours=_stack_pixels(others, mjd, *b.at(mjd), half, scale),
                            box=STACK_FIT["box"], max_shift=1.0, width_bounds=STACK_FIT["width_bounds"])
                if f is not None and f.ok:
                    flux = max(f.flux, 0.0)
        out.append(flux)
    return out


# ----------------------------------------------------------------------------- motion
def _solve(A: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Weighted least squares: parameters, covariance (not rescaled), chi2."""
    Aw, yw = A * w[:, None], y * w
    p, *_ = np.linalg.lstsq(Aw, yw, rcond=None)
    cov = np.linalg.pinv(Aw.T @ Aw)
    return p, cov, float(np.sum((Aw @ p - yw) ** 2))


def fit_track(dets: list[Detection], target: Body) -> dict | None:
    """Straight-line motion through the measured positions.

    Model, in arcsec east/north of a reference point, t in Julian years:
        east(t)  = E0 + pm_east  * (t - t0) + parallax * P_east(t)
        north(t) = N0 + pm_north * (t - t0) + parallax * P_north(t)

    SPHEREx sees a position every six months, from opposite sides of the
    Earth's orbit, so the parallax shift changes sign from pass to pass and
    would be mistaken for proper motion if left out. The catalogue parallax
    is used when there is one; with three passes or more the parallax is
    also fitted freely and reported next to it as a check.

    Positions more than 4 robust sigma from the fit are rejected (``used`` is
    cleared on them). Errors are the larger of the formal ones and a
    jackknife over groups of (sky pass, detector), which also feels offsets
    that a whole pass shares.
    """
    use = [d for d in dets if d.used]
    if len(use) < 3:
        return None
    mjd = np.array([d.mjd for d in use])
    t = year_of(mjd)
    sig = np.sqrt(np.array([d.pos_err_arcsec for d in use]) ** 2 + ERR_FLOOR_ARCSEC**2)
    w = 1.0 / sig
    t0 = float(np.sum(t * w**2) / np.sum(w**2))
    ref_ra, ref_dec = shift(target.ra, target.dec, target.pmra / 1000.0 * (t0 - target.epoch), target.pmdec / 1000.0 * (t0 - target.epoch))
    en = np.array([offset(d.ra, d.dec, ref_ra, ref_dec) for d in use])
    pe, pn = parallax_factors(ref_ra, ref_dec, mjd)
    passes = split_passes(mjd)
    n_pass = int(passes.max()) + 1
    span = float(t.max() - t.min())
    free_pm = n_pass >= 2 and span >= 0.25
    n = len(use)
    zero, one, dt = np.zeros(n), np.ones(n), t - t0

    def design(with_pm: bool, with_plx: bool) -> np.ndarray:
        cols = [np.concatenate([one, zero]), np.concatenate([zero, one])]
        if with_pm:
            cols += [np.concatenate([dt, zero]), np.concatenate([zero, dt])]
        if with_plx:
            cols.append(np.concatenate([pe, pn]))
        return np.column_stack(cols)

    plx = target.parallax / 1000.0
    cat_pm = np.array([target.pmra, target.pmdec]) / 1000.0

    def fit(keep: np.ndarray, with_pm: bool, with_plx: bool):
        y = np.concatenate([en[:, 0], en[:, 1]])
        if not with_plx:
            y = y - plx * np.concatenate([pe, pn])
        if not with_pm:
            y = y - np.concatenate([cat_pm[0] * dt, cat_pm[1] * dt])
        k2 = np.concatenate([keep, keep])
        A = design(with_pm, with_plx)
        p, cov, chi2 = _solve(A[k2], y[k2], np.concatenate([w, w])[k2])
        return p, cov, chi2, (y - A @ p).reshape(2, n)

    keep = np.ones(n, dtype=bool)
    for _ in range(4):  # reject outliers (a cosmic ray, a neighbour, a bad pixel)
        p, cov, chi2, res = fit(keep, free_pm, False)
        z = res * w  # residuals in units of each position's own error
        s = [max(1.4826 * float(np.median(np.abs(z[k][keep] - np.median(z[k][keep])))), 1.0) for k in (0, 1)]
        new = (np.abs(z[0]) <= 4.0 * s[0]) & (np.abs(z[1]) <= 4.0 * s[1])
        if new.sum() < max(3, 0.5 * n) or (new == keep).all():
            break
        keep = new
    p, cov, chi2, res = fit(keep, free_pm, False)
    for d, k in zip(use, keep):
        d.used = bool(k)
    nk = int(keep.sum())
    dof = max(2 * nk - len(p), 1)
    chi2_red = chi2 / dof
    err = np.sqrt(np.clip(np.diag(cov), 0, None) * max(chi2_red, 1.0))

    groups = passes * 10 + np.array([d.detector for d in use])
    labels = sorted(set(groups[keep].tolist()))

    def jackknife(with_pm: bool, with_plx: bool, n_par: int) -> np.ndarray | None:
        if len(labels) < 4:
            return None
        est = []
        for g in labels:
            sub = keep & (groups != g)
            if sub.sum() < 3 or (with_pm and int(len(set(passes[sub].tolist()))) < 2):
                return None
            est.append(fit(sub, with_pm, with_plx)[0])
        est = np.array(est)
        G = len(est)
        return np.sqrt((G - 1) / G * np.sum((est - est.mean(axis=0)) ** 2, axis=0))[:n_par]

    jk = jackknife(free_pm, False, len(p))
    if jk is not None:
        err = np.maximum(err, jk)

    e0, n0 = float(p[0]), float(p[1])
    ra0, dec0 = shift(ref_ra, ref_dec, e0, n0)
    pm = p[2:4] if free_pm else cat_pm
    out = {
        "model": ("linear motion" if free_pm else "position only (one sky pass)") + (", catalogue parallax" if plx else ""),
        "epoch": round(t0, 5), "ra": round(ra0, 8), "dec": round(dec0, 8),
        "pmra": round(float(pm[0]) * 1000.0, 2), "pmdec": round(float(pm[1]) * 1000.0, 2),
        "pmra_err": round(float(err[2]) * 1000.0, 2) if free_pm else None,
        "pmdec_err": round(float(err[3]) * 1000.0, 2) if free_pm else None,
        "ra_err_arcsec": round(float(err[0]), 3), "dec_err_arcsec": round(float(err[1]), 3),
        "parallax_mas": round(target.parallax, 3) if plx else None,
        "parallax_fit_mas": None, "parallax_fit_err": None,
        "n_detections": nk, "n_rejected": int(n - nk), "n_passes": n_pass, "span_years": round(span, 3),
        "rms_arcsec": round(float(np.sqrt(np.mean(res[:, keep] ** 2))), 3), "chi2_red": round(chi2_red, 2),
        "mjd_min": round(float(mjd[keep].min()), 5), "mjd_max": round(float(mjd[keep].max()), 5),
        "pm_fitted": bool(free_pm),
        # how the fit compares with the catalogue the prediction came from
        "catalog_offset_east": round(e0, 3), "catalog_offset_north": round(n0, 3),
        "catalog_dpmra": round(float(pm[0] - cat_pm[0]) * 1000.0, 2) if free_pm else None,
        "catalog_dpmdec": round(float(pm[1] - cat_pm[1]) * 1000.0, 2) if free_pm else None,
    }
    if free_pm and n_pass >= 3 and not all(d.method == "wings" for d in use):
        pp, cov_p, chi2_p, _ = fit(keep, True, True)
        err_p = np.sqrt(np.clip(np.diag(cov_p), 0, None) * max(chi2_p / max(2 * nk - 5, 1), 1.0))
        jk_p = jackknife(True, True, 5)
        if jk_p is not None:
            err_p = np.maximum(err_p, jk_p)
        # The telescope is turned around from one pass to the next, and so is
        # any small asymmetry of the PSF: an offset that changes sign every
        # pass, exactly like parallax. Measured on stars of known parallax it
        # is about 0.15 arcsec, which is the floor of this error.
        out["parallax_fit_mas"] = round(float(pp[4]) * 1000.0, 1)
        out["parallax_fit_err"] = round(float(math.hypot(err_p[4], PARALLAX_FLOOR_ARCSEC)) * 1000.0, 1)
    return out


# ----------------------------------------------------------------------------- brightness
def light_curve(dets: list[Detection]) -> dict | None:
    """Brightness against time for an object measured at a different
    wavelength in every exposure.

    Each exposure sees the object through another slice of the variable
    filter, so fluxes of different exposures cannot be compared directly.
    Instead every flux is divided by the object's own mean spectrum at that
    wavelength (the median of the measurements nearest in wavelength, from
    all dates). The ratio is 1 when the object is at its average brightness,
    whatever the wavelength."""
    best = None
    for method in ("psf", "wings"):  # the two kinds of flux are not on the same scale
        pts = [d for d in dets if d.method == method and d.flux_ujy is not None and np.isfinite(d.flux_ujy)
               and d.wave_um is not None and d.flux_ujy > 0]
        if len(pts) >= 30 and (best is None or len(pts) > len(best)):
            best = pts
    if best is None:
        return None
    pts = sorted(best, key=lambda d: d.wave_um)
    flux = np.array([d.flux_ujy for d in pts])
    mjd = np.array([d.mjd for d in pts])
    passes = split_passes(mjd)
    k = max(9, len(pts) // 12)
    level = np.ones(len(pts))  # brightness of each point's sky pass
    rel = np.ones(len(pts))
    for _ in range(4):
        # the mean spectrum from fluxes brought to a common level, so that a
        # bright pass does not pass for a bump in the spectrum
        norm = flux / level
        for i in range(len(pts)):
            lo = max(0, min(i - k // 2, len(pts) - k))
            rel[i] = flux[i] / np.median(norm[lo : lo + k])
        for g in range(int(passes.max()) + 1):
            m = passes == g
            if m.sum() >= 5:
                level[m] = max(float(np.median(rel[m])), 1e-6)
        level /= np.median(level)
    for d, r in zip(pts, rel):
        d.rel_flux = float(r)
    rows = []
    for g in range(int(passes.max()) + 1):
        m = passes == g
        if m.sum() < 5:
            continue
        med = float(np.median(rel[m]))
        sd = 1.4826 * float(np.median(np.abs(rel[m] - med)))
        rows.append({"mjd": round(float(mjd[m].mean()), 3), "rel_flux": round(med, 4),
                     "err": round(max(sd / math.sqrt(m.sum()), 1e-4), 4), "scatter": round(sd, 4), "n": int(m.sum())})
    days = []
    for day in sorted(set(np.floor(mjd).astype(int).tolist())):
        m = np.floor(mjd).astype(int) == day
        if m.sum() >= 3:
            days.append({"mjd": round(float(mjd[m].mean()), 3), "rel_flux": round(float(np.median(rel[m])), 4), "n": int(m.sum())})
    floor = FLUX_FLOOR[pts[0].method]
    out = {"method": pts[0].method, "n": len(pts), "passes": rows, "days": days, "amplitude": None, "significance": None,
           "floor": floor, "variable": False}
    if len(rows) >= 2:
        r = np.array([x["rel_flux"] for x in rows])
        e = np.hypot(np.array([x["err"] for x in rows]), floor * r)
        i, j = int(np.argmax(r)), int(np.argmin(r))
        out["amplitude"] = round(float(r[i] / r[j]), 3)  # brightest pass / faintest pass
        out["significance"] = round(float((r[i] - r[j]) / math.hypot(e[i], e[j])), 1)
        out["variable"] = bool(out["significance"] >= 3.0)
    return out


# ----------------------------------------------------------------------------- everything for one target
def near_path(target: Body, field: list[Body], mjd: float, radius_arcsec: float = NEIGHBOUR_ARCSEC) -> list[Body]:
    """Catalogue stars close to the target on a date, the target itself left out."""
    ra_t, dec_t = target.at(mjd)
    out = []
    for b in field:
        e, n = offset(*b.at(mjd), ra_t, dec_t)
        r = math.hypot(e, n)
        if r > radius_arcsec:
            continue
        same_id = bool(target.catalog_id) and b.catalog_id == target.catalog_id
        same_star = r < 2.0 and math.hypot(b.pmra - target.pmra, b.pmdec - target.pmdec) < 200.0 + 0.2 * math.hypot(target.pmra, target.pmdec)
        if same_id or same_star:
            continue
        out.append(b)
    return out


def measure_all(stamps: list[Stamp], target: Body, field: list[Body] | None = None, wave_maps: dict | None = None) -> dict:
    """Detections, track and light curve of one target from its stamps.

    ``field`` are catalogue stars around the target (Gaia), ``wave_maps``
    maps "collection_Ddetector" to the wavelength map of ``cutout.py``.
    """
    field = field or []
    wave_maps = wave_maps or {}
    stamps = sorted(stamps, key=lambda s: (s.exposure.mjd, s.exposure.detector))
    if not stamps:
        return {"target": target.__dict__, "detections": [], "track": None, "light_curve": None, "stats": {"stamps": 0}}
    mjds = np.array([s.exposure.mjd for s in stamps])
    passes = split_passes(mjds)
    mid = float(np.median(mjds))
    neighbours = near_path(target, field, mid)
    refs = [b for b in field if b.mag is not None and b.mag < 15.5][:40]

    def wmap(st: Stamp):
        return wave_maps.get(f"{st.exposure.collection}_D{st.exposure.detector}")

    singles: list[Detection | None] = []
    checks = []
    for st in stamps:
        singles.append(measure_stamp(st, target, neighbours, wmap(st)))
        if refs:
            c = pointing_check(st, refs, avoid=st.pixel(*target.at(st.exposure.mjd)))
            if c is not None:
                checks.append(c)

    dets: list[Detection] = []
    n_stacks = 0
    groups: dict[tuple[int, int], list[int]] = {}
    for i, st in enumerate(stamps):
        groups.setdefault((int(passes[i]), st.exposure.detector), []).append(i)
    priors: dict[int, list[float | None]] = {}
    for (_, det), idx in sorted(groups.items()):
        good = [singles[i] for i in idx if singles[i] is not None and singles[i].snr >= MIN_SNR]
        if len(good) >= max(3, 0.5 * len(idx)):
            dets += good
            continue
        if neighbours and det not in priors:
            priors[det] = neighbour_fluxes([[stamps[i] for i in ix] for (_, d2), ix in sorted(groups.items()) if d2 == det],
                                           target, neighbours)
        waves = []
        for i in idx:
            x, y = stamps[i].pixel(*target.at(stamps[i].exposure.mjd))
            wv, _ = wavelength_at(wmap(stamps[i]), x + stamps[i].x0, y + stamps[i].y0)
            if np.isfinite(wv):
                waves.append(wv)
        d = measure_stack([stamps[i] for i in idx], target, neighbours, waves, priors.get(det)) if len(idx) >= 3 else None
        if d is not None and d.snr >= STACK_MIN_SNR:
            dets.append(d)
            n_stacks += 1
        else:
            dets += good  # a few clear single exposures are still worth keeping
    dets.sort(key=lambda d: (d.mjd, d.detector))

    # saturated stars: brightness from a ring just outside the largest masked core
    wings = [d for d in dets if d.method == "wings"]
    if wings:
        r_in = float(np.ceil(np.percentile([d.mask_radius for d in wings], 90))) + 1.5
        by_uid = {s.exposure.uid: s for s in stamps}
        for d in wings:
            st = by_uid[f"{d.obs_id}_D{d.detector}"]
            flux, eflux = ring_flux(st.data, d.x, d.y, st.sigma, r_in, r_in + 3.0)
            d.flux_ujy, d.flux_err_ujy = flux * TO_UJY, eflux * TO_UJY
            d.flags = sorted(set(d.flags) | {"RING_FLUX"})
    # positions from saturated wings only count when there is little else
    n_core = sum(d.method != "wings" for d in dets)
    if n_core >= 20:
        for d in dets:
            if d.method == "wings":
                d.used = False
    track = fit_track(dets, target)
    curve = light_curve(dets)
    stats = {
        "stamps": len(stamps), "passes": int(passes.max()) + 1, "detections": len(dets),
        "single": sum(d.method == "psf" for d in dets), "wings": sum(d.method == "wings" for d in dets), "stacks": n_stacks,
        "neighbours": len(neighbours),
        "mjd_min": round(float(mjds.min()), 4), "mjd_max": round(float(mjds.max()), 4),
    }
    if checks:
        c = np.array(checks)
        stats.update(pointing_east=round(float(np.median(c[:, 0])), 3), pointing_north=round(float(np.median(c[:, 1])), 3),
                     pointing_scatter=round(1.4826 * float(np.median(np.hypot(c[:, 0] - np.median(c[:, 0]), c[:, 1] - np.median(c[:, 1])))), 3),
                     pointing_stamps=len(checks))
    return {
        "target": {**target.__dict__},
        "detections": [d.to_dict() for d in dets],
        "track": track,
        "light_curve": curve,
        "stats": stats,
    }


async def fetch_stamps(exposures: list[Exposure], target: Body, concurrency: int = 16, progress=None) -> tuple[list[Stamp], int]:
    """Read the stamp of every exposure. Returns the stamps and how many
    exposures failed (off-detector exposures are simply skipped)."""
    sem = asyncio.Semaphore(concurrency)
    out: list[Stamp] = []
    failed = 0
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), limits=limits) as client:

        async def one(e: Exposure) -> None:
            nonlocal failed
            ra, dec = target.at(e.mjd)
            async with sem:
                for attempt in range(3):
                    try:
                        st = await read_stamp(client, e, ra, dec)
                        if st is not None:
                            out.append(st)
                        break
                    except Exception:
                        if attempt == 2:
                            failed += 1
                        else:
                            await asyncio.sleep(1.0 + attempt)
                if progress:
                    progress()

        await asyncio.gather(*(one(e) for e in exposures))
    return out, failed
