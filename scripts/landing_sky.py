"""The photographs of the sky on the first page (web/landing/sky-*.webp).

They are cut from the SPHEREx DR1 all-sky map, which NASA/IPAC IRSA publishes
as a HiPS cube: the sky in HEALPix tiles of 512 x 512 pixels, one PNG for each
of the 102 colours (frame 0 is ``NpixN.png``, frame z is ``NpixN_z.png``),
already stretched for the eye. Three frames make the colours of a picture:

    red 3.29 um (frame 62), green 2.19 um (frame 46), blue 1.25 um (frame 22)

Credit: NASA/JPL-Caltech/IPAC. The four pictures of the page:

    python scripts/landing_sky.py web/landing/sky-milky-way.webp   --l 0 --b 0 --fov 70 --size 2000x1125 --order 3 --rot 16 --at 0.70,0.72 --quality 54 --black 8
    python scripts/landing_sky.py web/landing/sky-milky-way-m.webp --l 0 --b 0 --fov 44 --size 800x1440  --order 3 --rot 32 --at 0.60,0.80 --quality 56 --black 8
    python scripts/landing_sky.py web/landing/sky-orion.webp       --l 209.0 --b -19.4 --fov 27 --size 1600x750 --order 4 --at 0.24,0.62 --quality 58 --black 4
    python scripts/landing_sky.py web/landing/sky-rho-oph.webp     --l 353.2 --b 16.6  --fov 20 --size 1120x630 --order 4 --at 0.60,0.42 --quality 62 --black 6

``--l --b`` is a point of the sky in Galactic degrees, ``--at`` where it lands
in the picture (fractions of width and height), ``--fov`` the width of the
picture in degrees, ``--rot`` a turn about that point. The picture is sampled
at twice its size and reduced.

Two things are changed in the pixels (docs/viewer.md, "The first page"): the
black point is raised by ``--black``, and what one colour alone shows far from
its surroundings is filled from around it: the trail of a planet that moved
between the exposures of different colours, saturated pixels at the Galactic
centre. Single stars are left alone.

Needs, beside the packages of the service: astropy-healpix, scipy, pillow.
Tiles are kept in ``--cache`` (default: a folder in the temp directory).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import tempfile
from pathlib import Path

import httpx
import numpy as np

SURVEY = "https://irsa.ipac.caltech.edu/data/hips/SPHEREx/spherex_allsky_cube"
FRAMES = (62, 46, 22)   # red, green, blue: 3.29, 2.19 and 1.25 um
TILE = 512


def fetch_tile(client: httpx.Client, cache: Path, order: int, npix: int, frame: int):
    from PIL import Image

    name = f"Npix{npix}" + (f"_{frame}" if frame else "") + ".png"
    path = cache / f"Norder{order}" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{SURVEY}/Norder{order}/Dir{npix // 10000 * 10000}/{name}"
        for attempt in range(6):          # the archive answers 502 in bursts
            try:
                r = client.get(url)
                if r.status_code == 404:
                    path.write_bytes(b"")
                    break
                r.raise_for_status()
                path.write_bytes(r.content)
                break
            except httpx.HTTPError:
                if attempt == 5:
                    raise
    if path.stat().st_size == 0:
        return None
    return np.asarray(Image.open(path).convert("L"))


def compact(v):
    """Every second bit of v, packed: one of the two coordinates of a nested HEALPix index."""
    v = v & 0x5555555555555555
    for shift, mask in ((1, 0x3333333333333333), (2, 0x0F0F0F0F0F0F0F0F), (4, 0x00FF00FF00FF00FF),
                        (8, 0x0000FFFF0000FFFF), (16, 0x00000000FFFFFFFF)):
        v = (v | (v >> shift)) & mask
    return v


def sky_of_pixels(l0, b0, fov, w, h, rot, fx, fy):
    """Right ascension and declination of every pixel: an equidistant projection about (l0, b0)."""
    import astropy.units as u
    from astropy.coordinates import Galactic, SkyCoord

    x = (np.arange(w) - (w - 1) * fx) / w * np.radians(fov)
    y = ((h - 1) * fy - np.arange(h)) / w * np.radians(fov)
    X, Y = np.meshgrid(-x, y)             # longitude grows to the left, as on the sky
    if rot:
        c, s = np.cos(np.radians(rot)), np.sin(np.radians(rot))
        X, Y = c * X - s * Y, s * X + c * Y
    rho = np.hypot(X, Y)
    lat0 = np.radians(b0)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.arcsin(np.cos(rho) * np.sin(lat0) + np.where(rho > 0, Y * np.sin(rho) * np.cos(lat0) / rho, 0))
        lon = np.radians(l0) + np.arctan2(X * np.sin(rho), rho * np.cos(lat0) * np.cos(rho) - Y * np.sin(lat0) * np.sin(rho))
    icrs = SkyCoord(l=np.degrees(lon).ravel() * u.deg, b=np.degrees(lat).ravel() * u.deg, frame=Galactic).icrs
    return icrs.ra, icrs.dec


def cut(cache: Path, l0, b0, fov, w, h, order, rot=0.0, fx=0.5, fy=0.5):
    from astropy_healpix import HEALPix

    ra, dec = sky_of_pixels(l0, b0, fov, w, h, rot, fx, fy)
    index = HEALPix(nside=2 ** (order + 9), order="nested").lonlat_to_healpix(ra, dec).astype(np.int64)
    tile, inside = index >> 18, index & 0x3FFFF
    row, col = compact(inside), compact(inside >> 1)   # how a HiPS tile lays the nested index out
    out = np.zeros((h * w, 3), np.uint8)
    tiles = np.unique(tile)
    jobs = [(int(n), f) for n in tiles for f in FRAMES]
    with httpx.Client(timeout=40, headers={"User-Agent": "VanillaSphere/0.2 (first page pictures; https://vanillasphere.com)"}) as client:
        with cf.ThreadPoolExecutor(12) as pool:
            got = dict(zip(jobs, pool.map(lambda j: fetch_tile(client, cache, order, *j), jobs)))
    for n in tiles:
        here = tile == n
        for k, f in enumerate(FRAMES):
            if got[(int(n), f)] is not None:
                out[here, k] = got[(int(n), f)][row[here], col[here]]
    return out.reshape(h, w, 3)


def finish(picture: np.ndarray, w: int, h: int, black: float) -> np.ndarray:
    from PIL import Image
    from scipy import ndimage as ndi

    a = np.asarray(Image.fromarray(picture).resize((w, h), Image.LANCZOS)).astype(np.float32)
    around = np.stack([ndi.median_filter(a[..., c], size=21) for c in range(3)], -1)
    colour = a.max(-1) - a.min(-1)
    odd = (colour > around.max(-1) - around.min(-1) + 70) & (np.abs(a - around).max(-1) > 70)
    odd = ndi.binary_dilation(odd, iterations=3)
    labels, n = ndi.label(odd)
    if n:
        sizes = ndi.sum(odd, labels, range(1, n + 1))
        odd = np.isin(labels, 1 + np.flatnonzero(sizes >= 30))   # a single coloured star stays
    soft = ndi.gaussian_filter(odd.astype(np.float32), 1.5)[..., None]
    a = a * (1 - soft) + around * soft
    return np.clip((a - black) * 255.0 / (255.0 - black), 0, 255).astype(np.uint8)


def main() -> None:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--l", type=float, required=True)
    ap.add_argument("--b", type=float, required=True)
    ap.add_argument("--fov", type=float, required=True)
    ap.add_argument("--size", required=True, help="WIDTHxHEIGHT of the picture")
    ap.add_argument("--order", type=int, default=3, help="HiPS order of the tiles (the map goes to 5)")
    ap.add_argument("--rot", type=float, default=0.0)
    ap.add_argument("--at", default="0.5,0.5")
    ap.add_argument("--quality", type=int, default=60)
    ap.add_argument("--black", type=float, default=8)
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "landing-sky-tiles")
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    fx, fy = (float(v) for v in a.at.split(","))
    picture = cut(a.cache, a.l, a.b, a.fov, 2 * w, 2 * h, a.order, a.rot, fx, fy)
    Image.fromarray(finish(picture, w, h, a.black)).save(a.out, quality=a.quality, method=6)
    print(f"{a.out}: {w} x {h}, {a.out.stat().st_size // 1024} kB")


if __name__ == "__main__":
    main()
