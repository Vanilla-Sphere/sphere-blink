"""Cut a small, north-up, common-grid image out of one SPHEREx Level 2 exposure.

A Level 2 file is ~65 MB (IMAGE, FLAGS, VARIANCE, ZODI, EPSF, WCS-WAVE). We
never download it. Three small HTTP range requests against the public S3
mirror are enough:

  1. the first few blocks      -> IMAGE header (WCS, time, flag statistics)
  2. the needed IMAGE rows     -> pixels            } in parallel
     the FLAGS header + index  -> where each compressed row lives
  3. the needed FLAGS rows     -> per-pixel quality flags (RICE-compressed)

The per-pixel wavelength (SPHEREx uses linear variable filters, so wavelength
depends on detector position) comes from a small per-detector map that is
read once and reused.
"""

from __future__ import annotations

import hashlib
import re
import time
import warnings
from dataclasses import dataclass, field

import httpx
import numpy as np
from astropy.io import fits
from astropy.wcs import FITSFixedWarning, WCS
from scipy.interpolate import RectBivariateSpline
from scipy.ndimage import label, map_coordinates

from . import config
from .sia import Exposure

warnings.filterwarnings("ignore", category=FITSFixedWarning)
try:  # astropy logs a long SIP notice for every header; it is harmless here
    from astropy import log as _aplog

    _aplog.setLevel("WARNING")
except Exception:  # pragma: no cover
    pass

ARCSEC2_TO_SR = (np.pi / 180.0 / 3600.0) ** 2
DET_SIZE = 2040
BLOCK = 2880
ROW_BYTES = DET_SIZE * 4
FIRST_FETCH = 12 * BLOCK  # primary HDU + IMAGE header (25920 bytes in DR1/QR3)
FLAGS_FETCH = 14 * BLOCK  # FLAGS header + row index (17280 + 16320 bytes)
SATURATION_FLAGS = ("OVERFLOW", "NONLINEAR")
# OVERFLOW, TRANSIENT and SUR_ERROR come from the on-board fit of the charge
# ramp. They do not mean that the pixel has no value: the archive gives one,
# and it is nearly always a good one. A star above about 0.1 Jy sets them on
# its own core, and for 38 isolated 2MASS stars of 0.06-3.2 Jy (J and Ks) the
# aperture flux that keeps those pixels is 0.91-1.02 of the 2MASS flux, with
# 2-12 % scatter from exposure to exposure, the same as for cores without
# flags (docs/imaging-engine.md). On fainter sources four in five flagged
# pixels are within the noise of their neighbours; the rest are cosmic-ray
# hits. A pixel that really saturates has no value at all (NaN).
CORE_FLAGS = ("OVERFLOW", "TRANSIENT", "SUR_ERROR")
# Brightest pixel [MJy/sr above the sky] up to which this was checked against
# 2MASS (0.93-0.99 in every step of brightness from 30 to 2000). The archive
# has values up to about 3000; pixels above that have none.
CORE_MAX = 2000.0
# With more overflow pixels than this in the aperture the sum reads high (25 %
# with six or more, against exposures of the same star at the same wavelength
# with one or none): the brightest pixel filled up and its charge spilled into
# the neighbours, where it is counted a second time. Up to four it is 0.99-1.02.
CORE_OVERFLOWS = 4
CORE_SHARE = 0.8  # of the aperture's light in one pixel: more than a star puts there (0.75 at most), so a cosmic-ray hit
CORE_REACH = 1.5  # pixels around the target that hold the star's brightest pixel, and where no pixel may be missing

@dataclass
class Grid:
    """A north-up TAN grid centred on the target, shared by every frame."""

    ra: float
    dec: float
    npix: int
    scale_arcsec: float

    @classmethod
    def from_fov(cls, ra: float, dec: float, fov_arcmin: float, scale_arcsec: float | None = None):
        fov = min(max(fov_arcmin, 1.0), config.MAX_FOV_ARCMIN)
        if scale_arcsec is None:
            # Small fields are drawn at half the native pixel so sub-pixel
            # shifts between epochs are visible when blinking.
            scale_arcsec = config.NATIVE_PIXEL_ARCSEC / 2 if fov <= 12 else config.NATIVE_PIXEL_ARCSEC
        npix = int(round(fov * 60.0 / scale_arcsec))
        npix += 1 - npix % 2  # odd, so the target sits on the centre pixel
        return cls(ra=ra, dec=dec, npix=max(npix, 9), scale_arcsec=scale_arcsec)

    @property
    def wcs(self) -> WCS:
        w = WCS(naxis=2)
        c = (self.npix - 1) / 2.0
        w.wcs.crpix = [c + 1, c + 1]
        w.wcs.crval = [self.ra, self.dec]
        w.wcs.cdelt = [-self.scale_arcsec / 3600.0, self.scale_arcsec / 3600.0]
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        return w

    @property
    def fov_arcmin(self) -> float:
        return self.npix * self.scale_arcsec / 60.0

    def search_points(self) -> list[tuple[float, float]]:
        """Positions whose exposures make up this view.

        Views up to WIDE_FOV_ARCMIN use the exposures that contain the centre
        (every frame then covers most of the view). Wider ones are mosaics and
        also take exposures that cover only a part: every exposure containing
        any point of a lattice over the view (a 3.5 degree detector footprint
        cannot slip between points ~12 arcmin apart except by a corner tip).
        One archive query per point, run in parallel, is ~10x faster than one
        query for the whole area.
        """
        fov = self.fov_arcmin
        if fov <= config.WIDE_FOV_ARCMIN:
            return [(self.ra, self.dec)]
        k = 3 if fov <= 26 else 5
        t = np.linspace(0, self.npix - 1, k)
        lx, ly = np.meshgrid(t, t)
        ra, dec = self.wcs.pixel_to_world_values(lx.ravel(), ly.ravel())
        return [(float(a) % 360.0, float(d)) for a, d in zip(ra, dec)]

    def key(self) -> str:
        return f"{self.ra:.6f}_{self.dec:.6f}_{self.npix}_{self.scale_arcsec:.3f}"

    def to_dict(self) -> dict:
        return {"ra": self.ra, "dec": self.dec, "npix": self.npix, "scale_arcsec": self.scale_arcsec,
                "fov_arcmin": round(self.fov_arcmin, 3)}


@dataclass
class Frame:
    exposure: Exposure
    image: np.ndarray  # (npix, npix) float32 MJy/sr, background-subtracted, NaN = masked
    wave_um: float  # central wavelength at the target pixel
    bandwidth_um: float
    flux_ujy: float  # aperture flux at the target (for spectra / light curves)
    flux_err_ujy: float
    flags_at_target: list[str] = field(default_factory=list)
    good_fraction: float = 1.0

    def meta(self) -> dict:
        d = self.exposure.to_dict()
        d.update(
            wave_um=_r(self.wave_um, 4),
            bandwidth_um=_r(self.bandwidth_um, 4),
            flux_ujy=_r(self.flux_ujy, 3),
            flux_err_ujy=_r(self.flux_err_ujy, 3),
            flags_at_target=self.flags_at_target,
            good_fraction=round(self.good_fraction, 3),
        )
        return d


def _r(v: float, n: int):
    return round(float(v), n) if v is not None and np.isfinite(v) else None


# ----------------------------------------------------------------------------- FITS bytes
def _header_end(buf: bytes, start: int) -> int | None:
    """Offset just past the header that starts at ``start`` (None if incomplete)."""
    pos = start
    while pos + BLOCK <= len(buf):
        block = buf[pos : pos + BLOCK]
        for i in range(0, BLOCK, 80):
            if block[i : i + 8] == b"END     ":
                return pos + BLOCK
        pos += BLOCK
    return None


def _parse_header(buf: bytes, start: int, end: int, keep: "re.Pattern | None" = None) -> fits.Header:
    """Parse a header; with ``keep`` only matching cards are parsed (much faster
    than parsing the ~300 cards of a Level 2 header)."""
    text = buf[start:end].decode("ascii", "replace")
    if keep is not None:
        text = "".join(text[i : i + 80] for i in range(0, len(text), 80) if keep.match(text[i : i + 8]))
        text += "END" + " " * 77
    return fits.Header.fromstring(text)


_WCS_CARDS = re.compile(
    r"(XTENSION|BITPIX|NAXIS\d?|WCSAXES|CTYPE[12]|CRPIX[12]|CRVAL[12]|CDELT[12]|CUNIT[12]|"
    r"PC[12]_[12]|CD[12]_[12]|[AB]P?_\d_\d|[AB]P?_ORDER|LONPOLE|LATPOLE|RADESYS|EQUINOX)\s*$"
)
_FLAG_CARDS = re.compile(r"(XTENSION|BITPIX|NAXIS\d?|PCOUNT|THEAP|ZCMPTYPE|ZTILE\d|ZNAME\d|ZVAL\d|MSKN\d{4})\s*$")


def flag_planes(header: fits.Header) -> dict[str, int]:
    planes = {}
    for k in header:
        if k.startswith("MSKN"):
            planes[str(header[k]).strip()] = 1 << int(k[4:])
    return planes


def _bits(planes: dict[str, int], names) -> int:
    v = 0
    for name in names:
        v |= planes.get(name, 0)
    return v


# Running totals of archive traffic in this process (see Job.stats).
NET = {"requests": 0, "bytes": 0, "seconds": 0.0}


# The bucket is the fast way in; the archive's own copy is the second.
# After a few failures in a row the bucket is left alone for a minute, so a
# view of 1400 requests does not wait for 1400 timeouts.
MIRROR = {"failures": 0, "until": 0.0, "used": 0}
MIRROR_AFTER = 3
MIRROR_FOR_S = 60.0


def mirror_url(url: str) -> str | None:
    """The same file at the second source (None if there is none for it)."""
    base = config.S3_HTTP_BASE.rstrip("/") + "/"
    if config.MIRROR_HTTP_BASE and url.startswith(base):
        return config.MIRROR_HTTP_BASE.rstrip("/") + "/" + url[len(base):]
    return None


async def _get(client: httpx.AsyncClient, url: str, start: int, end: int) -> bytes:
    t0 = time.perf_counter()
    headers = {"Range": f"bytes={start}-{end - 1}"}
    second = mirror_url(url)
    if second and time.monotonic() < MIRROR["until"]:
        url, second = second, None  # the bucket is failing: straight to the second source
    try:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        if second:
            MIRROR["failures"] = 0
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0
        if not second or status == 416:
            raise
        if status != 404:  # a file the bucket does not have yet says nothing about the bucket's health
            MIRROR["failures"] += 1
            if MIRROR["failures"] >= MIRROR_AFTER:
                MIRROR["until"] = time.monotonic() + MIRROR_FOR_S
        r = await client.get(second, headers=headers, follow_redirects=True)
        r.raise_for_status()
        MIRROR["used"] += 1
    NET["requests"] += 1
    NET["bytes"] += len(r.content)
    NET["seconds"] += time.perf_counter() - t0
    return r.content



def _rice_rows(heap: bytes, lens: np.ndarray, offs: np.ndarray, base: int, blocksize: int) -> np.ndarray:
    from astropy.io.fits.hdu.compressed._codecs import Rice1

    codec = Rice1(blocksize=blocksize, bytepix=4, tilesize=DET_SIZE)
    buf = np.frombuffer(heap, dtype=np.uint8)
    rows = np.empty((len(lens), DET_SIZE), dtype=np.int32)
    for i, (n, o) in enumerate(zip(lens, offs)):
        rows[i] = codec.decode(buf[o - base : o - base + n])
    return rows


# ----------------------------------------------------------------------------- wavelength maps
WAVE_GRID = np.linspace(0, DET_SIZE - 1, 41)
_wave_maps: dict[tuple[str, int], np.ndarray] = {}


def wave_map_from_file(url: str) -> np.ndarray:
    """(2, 41, 41) grid of central wavelength / bandwidth [um] over the detector."""
    with fits.open(url, use_fsspec=True, fsspec_kwargs={"block_size": 2 * 1024 * 1024}) as hdul:
        ww = WCS(hdul["IMAGE"].header, fobj=hdul, key="W")
        xx, yy = np.meshgrid(WAVE_GRID, WAVE_GRID)
        lam, bw = ww.pixel_to_world_values(xx, yy)
    return np.stack([np.asarray(lam, dtype=np.float32), np.asarray(bw, dtype=np.float32)])


def wavelength_at(wmap: np.ndarray | None, x: float, y: float) -> tuple[float, float]:
    if wmap is None or not (0 <= x <= DET_SIZE - 1 and 0 <= y <= DET_SIZE - 1):
        return float("nan"), float("nan")
    step = WAVE_GRID[1] - WAVE_GRID[0]
    c = [[y / step], [x / step]]
    lam = map_coordinates(wmap[0], c, order=1, mode="nearest")[0]
    bw = map_coordinates(wmap[1], c, order=1, mode="nearest")[0]
    return float(lam), float(bw)


# ----------------------------------------------------------------------------- the cutout
EXACT_MAP_NPIX = 200  # grids up to this size get the exact transform for every pixel
MAP_STEP = 16  # wider grids: exact every 16 pixels, cubic interpolation between


def detector_coords(grid: Grid, wcs: WCS) -> tuple[np.ndarray, np.ndarray]:
    """Detector pixel (x, y) of every pixel of ``grid``.

    The distortion polynomial is inverted iteratively, ~0.4 us per point.
    That is nothing for a small view and a third of all CPU for a 7 x 7 cell
    mosaic, so wide grids evaluate it on a coarse lattice. The mapping is a
    smooth low-order polynomial; the interpolation error is below 1/1000 pixel
    (checked in tests/test_offline.py).
    """
    n = grid.npix
    if n > EXACT_MAP_NPIX:
        k = int(np.ceil((n - 1) / MAP_STEP)) + 1
        t = np.linspace(0, n - 1, k)
        ly, lx = np.meshgrid(t, t, indexing="ij")
        ra, dec = grid.wcs.pixel_to_world_values(lx, ly)
        try:
            cx, cy = wcs.world_to_pixel_values(ra, dec)
        except Exception:  # no convergence far outside the detector: do it exactly
            cx = None
        if cx is not None and np.isfinite(cx).all() and np.isfinite(cy).all():
            every = np.arange(n, dtype=float)
            return (RectBivariateSpline(t, t, cx)(every, every),
                    RectBivariateSpline(t, t, cy)(every, every))
    yy, xx = np.mgrid[0:n, 0:n]
    ra, dec = grid.wcs.pixel_to_world_values(xx, yy)
    return wcs.world_to_pixel_values(ra, dec)


async def cut_async(
    client: httpx.AsyncClient,
    exposure: Exposure,
    grid: Grid,
    wmap: np.ndarray | None = None,
    aperture_px: float = 2.0,
) -> Frame | None:
    """Return the exposure resampled onto ``grid``; None if it misses the grid."""
    url = exposure.http_url

    # 1. IMAGE header -------------------------------------------------------
    head = await _get(client, url, 0, FIRST_FETCH)
    p_end = _header_end(head, 0)
    i_end = _header_end(head, p_end) if p_end is not None else None
    while i_end is None:  # unusually long header: fetch more
        if len(head) > 60 * BLOCK:
            raise ValueError("IMAGE header not found")
        head += await _get(client, url, len(head), len(head) + FIRST_FETCH)
        p_end = _header_end(head, 0)
        i_end = _header_end(head, p_end) if p_end is not None else None
    hdr = _parse_header(head, p_end, i_end, _WCS_CARDS)
    if hdr.get("NAXIS1") != DET_SIZE or hdr.get("NAXIS2") != DET_SIZE or hdr.get("BITPIX") != -32:
        raise ValueError(f"unexpected IMAGE layout in {exposure.uid}")
    wcs = WCS(hdr)

    # Map every output pixel into the detector's pixel frame (SIP included).
    px, py = detector_coords(grid, wcs)
    inside = (px > -0.5) & (px < DET_SIZE - 0.5) & (py > -0.5) & (py < DET_SIZE - 0.5)
    if not inside.any():
        return None
    pad = 12  # room for the sky annulus of the aperture photometry
    x0 = int(max(np.floor(px[inside].min()) - pad, 0))
    x1 = int(min(np.ceil(px[inside].max()) + pad + 1, DET_SIZE))
    y0 = int(max(np.floor(py[inside].min()) - pad, 0))
    y1 = int(min(np.ceil(py[inside].max()) + pad + 1, DET_SIZE))

    # 2. IMAGE rows + FLAGS header/index, in parallel -------------------------
    import asyncio

    flags_hdr_off = i_end + (-(-DET_SIZE * ROW_BYTES // BLOCK)) * BLOCK
    img_bytes, fl = await asyncio.gather(
        _get(client, url, i_end + y0 * ROW_BYTES, i_end + y1 * ROW_BYTES),
        _get(client, url, flags_hdr_off, flags_hdr_off + FLAGS_FETCH),
    )
    sci = np.frombuffer(img_bytes, dtype=">f4").reshape(y1 - y0, DET_SIZE)[:, x0:x1].astype(np.float32)

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

    # 3. compressed FLAGS rows ------------------------------------------------
    lo, hi = int(offs.min()), int((offs + lens).max())
    heap = await _get(client, url, heap_off + lo, heap_off + hi)
    flags = _rice_rows(heap, lens, offs, lo, blocksize)[:, x0:x1]

    planes = flag_planes(fhdr)
    bad = (flags & _bits(planes, config.BAD_FLAG_NAMES)) != 0
    saturated = (flags & _bits(planes, SATURATION_FLAGS)) != 0
    if saturated.any():
        # Only cores of bright stars: lone overflow pixels are cosmic-ray hits.
        lab, n_lab = label(saturated)
        sizes = np.bincount(lab.ravel(), minlength=n_lab + 1)
        saturated = (sizes >= 4)[lab] & (lab > 0)

    data = sci.copy()
    data[bad | ~np.isfinite(data)] = np.nan
    background = _background_plane(data)
    data -= background
    # Saturated cores of bright stars are masked by the pipeline; show them at
    # the brightest level instead of as a hole.
    display = data.copy()
    if saturated.any() and np.isfinite(data).any():
        display[saturated] = np.nanmax(data)

    # Resample (bilinear) onto the common grid; masked pixels stay NaN.
    cx, cy = px - x0, py - y0
    ok = np.isfinite(display)
    val = map_coordinates(np.where(ok, display, 0.0), [cy, cx], order=1, mode="constant", cval=0.0)
    wgt = map_coordinates(ok.astype(np.float32), [cy, cx], order=1, mode="constant", cval=0.0)
    out = np.where((wgt > 0.6) & inside, val / np.maximum(wgt, 1e-6), np.nan).astype(np.float32)

    tx, ty = (float(v) for v in wcs.world_to_pixel_values(grid.ra, grid.dec))
    wave, bw = wavelength_at(wmap, tx, ty)
    flux, err, tflags = aperture_flux(data, flags, planes, tx - x0, ty - y0, aperture_px, raw=sci - background)

    return Frame(
        exposure=exposure,
        image=out,
        wave_um=wave,
        bandwidth_um=bw,
        flux_ujy=flux,
        flux_err_ujy=err,
        flags_at_target=tflags,
        good_fraction=float(np.isfinite(out).mean()),
    )


def cut(exposure: Exposure, grid: Grid, wmap: np.ndarray | None = None) -> Frame | None:
    """Blocking convenience wrapper around :func:`cut_async`."""
    import asyncio

    async def run():
        async with httpx.AsyncClient(timeout=60) as client:
            return await cut_async(client, exposure, grid, wmap)

    return asyncio.run(run())


def _background_plane(data: np.ndarray) -> np.ndarray:
    """Robust sky level (+ gradient for larger cutouts) to subtract.

    The zodiacal light changes with date and, through the variable filter,
    across the detector; removing it makes frames from different dates match.
    """
    ok = np.isfinite(data)
    if ok.sum() < 30:
        return np.zeros_like(data)
    med = float(np.nanmedian(data[ok]))
    if min(data.shape) < 60:
        return np.full_like(data, med)
    yy, xx = np.mgrid[0 : data.shape[0], 0 : data.shape[1]]
    # A plane has three parameters: ~40 000 evenly spread pixels pin it down
    # as well as the half million of a wide cutout.
    step = max(1, int(np.ceil(np.sqrt(data.size / 40_000))))
    d, x, y = data[::step, ::step], xx[::step, ::step], yy[::step, ::step]
    ok = np.isfinite(d)
    if ok.sum() < 30:
        return np.full_like(data, med)
    sel = ok
    coef = np.array([med, 0.0, 0.0])
    for _ in range(3):
        resid = d - (coef[0] + coef[1] * x + coef[2] * y)
        s = 1.4826 * np.nanmedian(np.abs(resid[sel] - np.nanmedian(resid[sel])))
        sel = ok & (np.abs(resid) < 2.5 * max(s, 1e-12))
        if sel.sum() < 30:
            break
        A = np.column_stack([np.ones(sel.sum()), x[sel], y[sel]])
        coef, *_ = np.linalg.lstsq(A, d[sel], rcond=None)
    return (coef[0] + coef[1] * xx + coef[2] * yy).astype(np.float32)


def aperture_flux(data, flags, planes, x, y, r, raw=None):
    """Sky-subtracted circular aperture flux [uJy] at detector-cutout (x, y).

    ``data`` has the flagged pixels masked (NaN). ``raw`` is the same cutout
    with the archive's value in every pixel. Pixels whose only flags are
    ``CORE_FLAGS`` are summed at that value when the aperture then holds a
    star (``_flagged_but_measured``); otherwise the flags are believed.

    A pixel that is missing next to the target (within ``CORE_REACH``), or
    that overflowed, cannot be replaced by the mean of the others, because it
    holds a large part of the light: there is no flux then (NaN). Any other
    missing pixel is at the rim of the aperture, where a star has about 1 %
    of its light per pixel, and is left out of the sum; the flux then carries
    the mark ``PARTIAL``. (Counting it as the mean of the others, as before,
    read 9 % high on 2MASS stars.)

    The error is empirical: the scatter of the sky annulus scaled to the
    aperture area.
    """
    h, w = data.shape
    if not (0 <= x < w and 0 <= y < h):
        return float("nan"), float("nan"), []
    yy, xx = np.mgrid[0:h, 0:w]
    d2 = (xx - x) ** 2 + (yy - y) ** 2
    ap = d2 <= r * r
    names = _names(flags[ap], planes)
    good = ap & np.isfinite(data)
    ann = (d2 >= (r + 3) ** 2) & (d2 <= (r + 8) ** 2) & np.isfinite(data)
    if ann.sum() < 20:
        return float("nan"), float("nan"), names
    sky = float(np.median(data[ann]))
    sky_sigma = 1.4826 * float(np.median(np.abs(data[ann] - sky)))
    values = data
    if raw is not None and not good[ap].all():
        measured = _flagged_but_measured(raw, flags, planes, ap, good, d2, sky)
        if measured is not None:
            good, values = measured, raw
    # Not summed: pixels next to the target, and pixels that overflowed
    # wherever they are in the aperture (they are the bright ones).
    lost = ap & ~good
    heavy = (d2 <= CORE_REACH**2) | ((flags & _bits(planes, SATURATION_FLAGS)) != 0)
    if (lost & heavy).any() or good.sum() < max(3, 0.6 * ap.sum()):
        return float("nan"), float("nan"), names
    to_ujy = (config.NATIVE_PIXEL_ARCSEC**2) * ARCSEC2_TO_SR * 1e12  # MJy/sr * sr -> uJy
    flux = float(np.sum(values[good] - sky)) * to_ujy
    err = sky_sigma * np.sqrt(ap.sum()) * to_ujy
    if lost.any():
        names = sorted(set(names) | {"PARTIAL"})  # not a flag of the archive: a rim pixel is not in the sum
    return flux, float(err), names


def _flagged_but_measured(raw, flags, planes, ap, good, d2, sky):
    """The aperture pixels to sum when its flagged pixels are measurements,
    else None.

    They are when the pixels flagged only with ``CORE_FLAGS``, taken at the
    archive's value, make a star together with the unflagged ones: the
    brightest pixel is next to the target, within the range that was checked
    against 2MASS, and does not hold more of the light than a star puts into
    one pixel. A cosmic-ray hit fails this: it is one pixel, or a streak,
    far brighter than the source it lands on. A core in which more than
    ``CORE_OVERFLOWS`` pixels overflowed, or one has no value, is saturated.
    """
    overflow = ap & ((flags & _bits(planes, SATURATION_FLAGS)) != 0)
    if (overflow & ~np.isfinite(raw)).any() or overflow.sum() > CORE_OVERFLOWS:
        return None  # a pixel of the aperture is saturated, or spilled over: the core cannot be summed
    bad_bits = _bits(planes, config.BAD_FLAG_NAMES)
    mild = (flags & bad_bits & ~_bits(planes, CORE_FLAGS)) == 0  # no masking flag but those three
    use = ap & (good | (mild & np.isfinite(raw)))
    if not (use & ~good).any():
        return None
    v = np.where(use, raw - sky, -np.inf)
    k = int(np.argmax(v))
    peak, total = float(v.flat[k]), float(np.sum(raw[use] - sky))
    if not (0 < peak <= CORE_MAX and d2.flat[k] <= CORE_REACH**2 and peak <= CORE_SHARE * total):
        return None
    return use


def _names(values: np.ndarray, planes: dict[str, int]) -> list[str]:
    v = int(np.bitwise_or.reduce(values.ravel())) if values.size else 0
    return sorted(n for n, bit in planes.items() if v & bit)


def cache_key(exposure: Exposure, grid: Grid) -> str:
    raw = f"{exposure.uid}|{exposure.collection}|{grid.key()}|v4"
    return hashlib.sha1(raw.encode()).hexdigest()
