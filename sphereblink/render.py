"""Turn cutout frames into display epochs, PNG frames and animated GIFs."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from astropy.time import Time
from PIL import Image, ImageDraw, ImageFont

from .cutout import Frame

DETECTOR_RANGE_UM = {1: (0.75, 1.09), 2: (1.10, 1.62), 3: (1.63, 2.42), 4: (2.42, 3.82), 5: (3.83, 4.41), 6: (4.42, 5.00)}


@dataclass
class Epoch:
    label: str  # UTC date (or date range) shown on the frame
    mjd: float
    image: np.ndarray  # normalised (unit = robust sigma), NaN = no data
    members: list[Frame]

    def meta(self) -> dict:
        waves = [f.wave_um for f in self.members if np.isfinite(f.wave_um)]
        flags = sorted({n for f in self.members for n in f.flags_at_target})
        return {
            "label": self.label,
            "mjd": round(self.mjd, 5),
            "n_exposures": len(self.members),
            "detectors": sorted({f.exposure.detector for f in self.members}),
            "coverage": round(float(np.isfinite(self.image).mean()), 3),
            "wave_um": [round(min(waves), 3), round(max(waves), 3)] if waves else None,
            "flags_at_target": flags,
            "exposures": [f.exposure.uid for f in self.members],
        }


def _robust_sigma(a: np.ndarray) -> float:
    v = a[np.isfinite(a)]
    if v.size < 10:
        return float("nan")
    mad = np.median(np.abs(v - np.median(v)))
    s = 1.4826 * mad
    return float(s) if s > 0 else float(np.std(v) or np.nan)


def _normalised(f: Frame) -> np.ndarray | None:
    s = _robust_sigma(f.image)
    if not np.isfinite(s):
        return None
    return f.image / s


def _date(mjd: float) -> str:
    return Time(mjd, format="mjd").to_datetime().strftime("%Y-%m-%d")


def build_epochs(
    frames: list[Frame],
    detectors: list[int] | None = None,
    group: str = "day",
    min_coverage: float = 0.2,
) -> list[Epoch]:
    """Combine frames into display epochs.

    group="exposure": one epoch per exposure (finest time sampling)
    group="day":      all chosen detectors observed on the same UTC day are
                      averaged (after per-frame noise normalisation) - this
                      adds the 102 bands together to raise signal-to-noise
    group="visit":    same, but over each ~6-month sky pass (gaps > 20 days)
    """
    use = [f for f in frames if (not detectors or f.exposure.detector in detectors) and f.good_fraction >= min_coverage]
    use.sort(key=lambda f: f.exposure.mjd)
    if not use:
        return []

    groups: list[list[Frame]] = []
    if group == "exposure":
        groups = [[f] for f in use]
    elif group == "visit":
        cur = [use[0]]
        for f in use[1:]:
            if f.exposure.mjd - cur[-1].exposure.mjd > 20:
                groups.append(cur)
                cur = []
            cur.append(f)
        groups.append(cur)
    else:  # day
        by_day: dict[int, list[Frame]] = {}
        for f in use:
            by_day.setdefault(int(np.floor(f.exposure.mjd)), []).append(f)
        groups = [by_day[k] for k in sorted(by_day)]

    epochs = []
    for g in groups:
        stack = [n for n in (_normalised(f) for f in g) if n is not None]
        if not stack:
            continue
        cube = np.stack(stack)
        with np.errstate(all="ignore"), _quiet():
            img = np.nanmean(cube, axis=0).astype(np.float32)
        mjds = [f.exposure.mjd for f in g]
        d0, d1 = _date(min(mjds)), _date(max(mjds))
        label = d0 if d0 == d1 else f"{d0} .. {d1}"
        epochs.append(Epoch(label=label, mjd=float(np.mean(mjds)), image=img, members=g))
    return epochs


class _quiet:
    def __enter__(self):
        import warnings

        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *a):
        self._w.__exit__(*a)


def difference(epochs: list[Epoch]) -> list[Epoch]:
    """Subtract the median sky of all epochs: static stars vanish, movers and
    variables remain (positive where the source is now, negative where it was)."""
    if len(epochs) < 2:
        return epochs
    cube = np.stack([e.image for e in epochs])
    with _quiet():
        ref = np.nanmedian(cube, axis=0)
    return [Epoch(e.label, e.mjd, e.image - ref, e.members) for e in epochs]


def shared_limits(epochs: list[Epoch], diff: bool = False) -> tuple[float, float]:
    vals = np.concatenate([e.image[np.isfinite(e.image)].ravel() for e in epochs]) if epochs else np.array([0.0])
    if vals.size == 0:
        return -1.0, 1.0
    if diff:
        m = float(np.percentile(np.abs(vals), 99.5)) or 1.0
        return -m, m
    lo, hi = np.percentile(vals, [1.0, 99.7])
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def to_rgb8(img: np.ndarray, lo: float, hi: float, diff: bool = False) -> np.ndarray:
    """Asinh stretch with shared limits; NaN shown as dark blue-grey."""
    finite = np.isfinite(img)
    x = np.where(finite, img, lo)
    if diff:
        t = np.clip(x / hi, -1, 1)
        g = np.arcsinh(4 * t) / np.arcsinh(4)
        rgb = np.stack([np.clip(0.5 + 0.5 * g, 0, 1)] * 3, axis=-1)  # white = brighter now, black = fainter
    else:
        t = np.clip((x - lo) / (hi - lo), 0, 1)
        v = np.arcsinh(10 * t) / np.arcsinh(10)
        rgb = np.stack([v, v, v], axis=-1)
    out = (rgb * 255).astype(np.uint8)
    out[~finite] = (24, 28, 40)
    return out[::-1]  # FITS rows run bottom-up; images top-down (north up)


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frame_image(
    epoch: Epoch,
    lo: float,
    hi: float,
    size: int = 480,
    diff: bool = False,
    title: str = "",
    crosshair: bool = True,
) -> Image.Image:
    rgb = to_rgb8(epoch.image, lo, hi, diff)
    im = Image.fromarray(rgb).resize((size, size), Image.NEAREST)
    d = ImageDraw.Draw(im)
    f = _font(max(12, size // 32))
    c = size / 2
    if crosshair:
        g, L = size * 0.03, size * 0.06
        col = (255, 200, 0)
        for a, b in (((c - g - L, c), (c - g, c)), ((c + g, c), (c + g + L, c)), ((c, c - g - L), (c, c - g)), ((c, c + g), (c, c + g + L))):
            d.line([a, b], fill=col, width=2)
    dets = ",".join(f"D{x}" for x in sorted({m.exposure.detector for m in epoch.members}))
    text = f"{epoch.label}   {dets}"
    fs = int(getattr(f, 'size', 12))
    d.rectangle([0, 0, size, fs + 10], fill=(0, 0, 0))
    d.text((6, 4), text, fill=(255, 255, 255), font=f)
    if title:
        d.text((6, size - fs - 8), title, fill=(255, 220, 120), font=f)
    d.text((size - fs * 4, 4), "N up, E left", fill=(150, 190, 230), font=_font(max(9, fs - 3)))
    return im


def encode_cube(epochs: list[Epoch]) -> tuple[bytes, dict]:
    """Pack epoch images for the browser: uint16, asinh-compressed, 0 = no data.

    value = sinh(a_lo + (q - 1) / 65534 * (a_hi - a_lo)), in units of the
    per-exposure sky noise (sigma).
    """
    if not epochs:
        return b"", {"a_lo": 0.0, "a_hi": 1.0, "n": 0}
    cube = np.stack([e.image for e in epochs]).astype(np.float32)
    finite = np.isfinite(cube)
    a = np.arcsinh(np.where(finite, cube, 0.0))
    a_lo = float(a[finite].min()) if finite.any() else 0.0
    a_hi = float(a[finite].max()) if finite.any() else 1.0
    if a_hi <= a_lo:
        a_hi = a_lo + 1.0
    q = np.where(finite, 1 + np.round((a - a_lo) / (a_hi - a_lo) * 65534), 0).astype("<u2")
    return q.tobytes(), {"a_lo": a_lo, "a_hi": a_hi, "n": len(epochs)}


def png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def gif_bytes(images: list[Image.Image], fps: float = 2.0) -> bytes:
    buf = io.BytesIO()
    dur = int(1000 / max(fps, 0.2))
    durations = [dur] * len(images)
    if durations:
        durations[-1] = dur + 1200  # pause on the last frame
    images[0].save(buf, format="GIF", save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=False)
    return buf.getvalue()
