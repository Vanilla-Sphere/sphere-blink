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
import warnings
from dataclasses import dataclass, field

import httpx
import numpy as np
from astropy.io import fits
from astropy.wcs import FITSFixedWarning, WCS
from scipy.ndimage import map_coordinates

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


async def _get(client: httpx.AsyncClient, url: str, start: int, end: int) -> bytes:
    r = await client.get(url, headers={"Range": f"bytes={start}-{end - 1}"})
    r.raise_for_status()
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
    n = grid.npix
    yy, xx = np.mgrid[0:n, 0:n]
    ra_o, dec_o = grid.wcs.pixel_to_world_values(xx, yy)
    px, py = wcs.world_to_pixel_values(ra_o, dec_o)
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

    data = sci.copy()
    data[bad | ~np.isfinite(data)] = np.nan
    data -= _background_plane(data)
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
    flux, err, tflags = aperture_flux(data, flags, planes, tx - x0, ty - y0, aperture_px)

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
    sel = ok
    coef = np.array([med, 0.0, 0.0])
    for _ in range(3):
        resid = data - (coef[0] + coef[1] * xx + coef[2] * yy)
        s = 1.4826 * np.nanmedian(np.abs(resid[sel] - np.nanmedian(resid[sel])))
        sel = ok & (np.abs(resid) < 2.5 * max(s, 1e-12))
        if sel.sum() < 30:
            break
        A = np.column_stack([np.ones(sel.sum()), xx[sel], yy[sel]])
        coef, *_ = np.linalg.lstsq(A, data[sel], rcond=None)
    return (coef[0] + coef[1] * xx + coef[2] * yy).astype(np.float32)


def aperture_flux(data, flags, planes, x, y, r):
    """Sky-subtracted circular aperture flux [uJy] at detector-cutout (x, y).

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
    if good.sum() < max(3, 0.6 * ap.sum()) or ann.sum() < 20:
        return float("nan"), float("nan"), names
    sky = float(np.median(data[ann]))
    sky_sigma = 1.4826 * float(np.median(np.abs(data[ann] - sky)))
    to_ujy = (config.NATIVE_PIXEL_ARCSEC**2) * ARCSEC2_TO_SR * 1e12  # MJy/sr * sr -> uJy
    scale = ap.sum() / good.sum()
    flux = float(np.sum(data[good] - sky)) * scale * to_ujy
    err = sky_sigma * np.sqrt(ap.sum()) * to_ujy
    return flux, float(err), names


def _names(values: np.ndarray, planes: dict[str, int]) -> list[str]:
    v = int(np.bitwise_or.reduce(values.ravel())) if values.size else 0
    return sorted(n for n, bit in planes.items() if v & bit)


def cache_key(exposure: Exposure, grid: Grid) -> str:
    raw = f"{exposure.uid}|{exposure.collection}|{grid.key()}|v2"
    return hashlib.sha1(raw.encode()).hexdigest()
