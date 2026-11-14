"""Turn cutout frames into display epochs, PNG frames and animated GIFs."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from astropy.time import Time
from PIL import Image, ImageDraw, ImageFont

from .cutout import Frame

DETECTOR_RANGE_UM = {1: (0.75, 1.09), 2: (1.10, 1.62), 3: (1.63, 2.42), 4: (2.42, 3.82), 5: (3.83, 4.41), 6: (4.42, 5.00)}


# False colour: longest wavelengths in red, shortest in blue. Each channel
# uses its preferred detector and falls back to the neighbouring one.
RGB_CHANNELS = (("R", 6, 5), ("G", 4, 3), ("B", 2, 1))


@dataclass
class Epoch:
    label: str  # UTC date (or date range) shown on the frame
    mjd: float
    image: np.ndarray  # normalised (unit = robust sigma), NaN = no data; (n, n) or colour (3, n, n)
    members: list[Frame]
    channels: list[dict] | None = None  # colour only: where each of R, G, B came from

    @property
    def planes(self) -> int:
        return 3 if self.image.ndim == 3 else 1

    def meta(self) -> dict:
        waves = [f.wave_um for f in self.members if np.isfinite(f.wave_um)]
        flags = sorted({n for f in self.members for n in f.flags_at_target})
        extra = {"channels": self.channels} if self.channels is not None else {}
        return {
            **extra,
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
    epochs = []
    for g in _groups(use, group):
        img = _mean_image(g)
        if img is None:
            continue
        mjds = [f.exposure.mjd for f in g]
        epochs.append(Epoch(label=_label(mjds), mjd=float(np.mean(mjds)), image=img, members=g))
    return epochs


def _groups(use: list[Frame], group: str) -> list[list[Frame]]:
    use = sorted(use, key=lambda f: f.exposure.mjd)
    if not use:
        return []
    if group == "exposure":
        return [[f] for f in use]
    if group == "visit":
        groups, cur = [], [use[0]]
        for f in use[1:]:
            if f.exposure.mjd - cur[-1].exposure.mjd > 20:
                groups.append(cur)
                cur = []
            cur.append(f)
        groups.append(cur)
        return groups
    by_day: dict[int, list[Frame]] = {}
    for f in use:
        by_day.setdefault(int(np.floor(f.exposure.mjd)), []).append(f)
    return [by_day[k] for k in sorted(by_day)]


def _mean_image(frames: list[Frame]) -> np.ndarray | None:
    stack = [n for n in (_normalised(f) for f in frames) if n is not None]
    if not stack:
        return None
    with np.errstate(all="ignore"), _quiet():
        return np.nanmean(np.stack(stack), axis=0).astype(np.float32)


def _label(mjds: list[float]) -> str:
    d0, d1 = _date(min(mjds)), _date(max(mjds))
    return d0 if d0 == d1 else f"{d0} .. {d1}"


def build_color_epochs(frames: list[Frame], group: str = "day", min_coverage: float = 0.2, donors: int = 8) -> list[Epoch]:
    """False-colour epochs: red = D6 (else D5), green = D4 (else D3), blue = D2 (else D1).

    SPHEREx sees a position with only one or two detectors on a given day, so
    a channel (or a hole in it) that was not observed in an epoch is borrowed
    from the nearest epoch that has it. ``Epoch.channels`` records the
    detector and date of every channel, and whether it was borrowed, so a
    frame is never presented as a simultaneous three-colour observation.
    """
    use = [f for f in frames if f.good_fraction >= min_coverage]
    groups = _groups(use, group)
    if not groups:
        return []
    mjd = [float(np.mean([f.exposure.mjd for f in g])) for g in groups]
    own: list[list[tuple[np.ndarray, int, float] | None]] = []
    for g in groups:
        row = []
        for _, pref, fallback in RGB_CHANNELS:
            sel = [f for f in g if f.exposure.detector == pref] or [f for f in g if f.exposure.detector == fallback]
            img = _mean_image(sel) if sel else None
            row.append(None if img is None else (img, sel[0].exposure.detector, float(np.mean([f.exposure.mjd for f in sel]))))
        own.append(row)

    # White balance: put every detector on one display scale (0 = its median
    # sky, 1 = its bright stars) so a channel keeps its level when the
    # detector feeding it changes from one date to the next.
    for det in range(1, 7):
        imgs = [o[0] for row in own for o in row if o is not None and o[1] == det]
        if not imgs:
            continue
        vals = np.concatenate([a[np.isfinite(a)] for a in imgs])
        if vals.size < 10:
            continue
        med = float(np.median(vals))
        sig = 1.4826 * float(np.median(np.abs(vals - med))) or 1.0
        span = max(float(np.percentile(vals, 99.7)) - med, 10.0 * sig)
        for a in imgs:
            a -= med
            a /= span

    have = [[j for j in range(len(groups)) if own[j][c] is not None] for c in range(3)]
    shape = groups[0][0].image.shape
    epochs = []
    for gi, g in enumerate(groups):
        planes, info = [], []
        for c, (name, _, _) in enumerate(RGB_CHANNELS):
            mine = own[gi][c]
            near = sorted((j for j in have[c] if j != gi), key=lambda j: (abs(mjd[j] - mjd[gi]), j))[:donors]
            if mine is not None:
                img, det, when, borrowed = mine[0].copy(), mine[1], mine[2], False
            elif near:
                d = own[near[0]][c]
                img, det, when, borrowed = d[0].copy(), d[1], d[2], True
            else:
                planes.append(np.full(shape, np.nan, dtype=np.float32))
                info.append({"color": name, "detector": None, "date": None, "borrowed": False})
                continue
            for j in near:  # fill masked pixels from the nearest epochs of the same channel
                hole = ~np.isfinite(img)
                if not hole.any():
                    break
                d = own[j][c][0]
                use_px = hole & np.isfinite(d)
                img[use_px] = d[use_px]
            planes.append(img)
            info.append({"color": name, "detector": det, "date": _date(when), "borrowed": borrowed})
        mjds = [f.exposure.mjd for f in g]
        epochs.append(Epoch(label=_label(mjds), mjd=mjd[gi], image=np.stack(planes), members=g, channels=info))
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
    return [Epoch(e.label, e.mjd, e.image - ref, e.members, e.channels) for e in epochs]


def shared_limits(epochs: list[Epoch], diff: bool = False):
    """One stretch for every frame of a movie, so brightness changes are real.
    Returns (lo, hi), or one (lo, hi) per channel for colour epochs."""
    if epochs and epochs[0].planes == 3:
        return [_limits([e.image[c] for e in epochs], diff, color=True) for c in range(3)]
    return _limits([e.image for e in epochs], diff)


COLOR_FLOOR_SIGMA = 1.0  # colour frames start this far above the sky, or noise shows as coloured speckle


def _limits(images: list[np.ndarray], diff: bool, color: bool = False) -> tuple[float, float]:
    vals = np.concatenate([a[np.isfinite(a)].ravel() for a in images]) if images else np.array([0.0])
    if vals.size == 0:
        return -1.0, 1.0
    if diff:
        m = float(np.percentile(np.abs(vals), 99.5)) or 1.0
        return -m, m
    lo, hi = np.percentile(vals, [1.0, 99.7])
    if color:
        med = float(np.median(vals))
        lo = med + COLOR_FLOOR_SIGMA * 1.4826 * float(np.median(np.abs(vals - med)))
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


NO_DATA_RGB = (24, 28, 40)


def to_rgb8(img: np.ndarray, lo, hi=None, diff: bool = False) -> np.ndarray:
    """Asinh stretch with shared limits; NaN shown as dark blue-grey."""
    if img.ndim == 3:
        return _color_rgb8(img, lo, diff)
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
    out[~finite] = NO_DATA_RGB
    return out[::-1]  # FITS rows run bottom-up; images top-down (north up)


def _color_rgb8(img: np.ndarray, limits, diff: bool) -> np.ndarray:
    """Colour composite. Brightness follows an asinh curve of the mean of the
    three channels and the channel ratios are kept, so a star keeps its colour
    from its faint wings to its core. A pixel missing in one channel takes
    the mean of the others; missing in all three is 'no data'."""
    finite = np.isfinite(img)
    any_ok = finite.any(axis=0)
    t = np.empty(img.shape, dtype=np.float32)
    for c, (lo, hi) in enumerate(limits):
        x = np.where(finite[c], img[c], 0.0)
        t[c] = np.clip(x / hi, -1, 1) if diff else np.clip((x - lo) / (hi - lo), 0, 1)
    n_ok = np.maximum(finite.sum(axis=0), 1)
    mean = np.where(finite, t, 0.0).sum(axis=0) / n_ok
    t = np.where(finite, t, mean[None])
    if diff:
        rgb = 0.5 + 0.5 * np.arcsinh(4 * t) / np.arcsinh(4)
    else:
        lum = t.mean(axis=0)
        scale = np.arcsinh(10 * lum) / np.arcsinh(10) / np.maximum(lum, 1e-6)
        rgb = t * scale[None]
        rgb = rgb / np.maximum(rgb.max(axis=0), 1.0)[None]
    out = (np.clip(np.moveaxis(rgb, 0, -1), 0, 1) * 255).astype(np.uint8)
    out[~any_ok] = NO_DATA_RGB
    return out[::-1]


def _font(size: int):
    for name in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frame_image(
    epoch: Epoch,
    limits,
    size: int = 480,
    diff: bool = False,
    title: str = "",
    crosshair: bool = True,
) -> Image.Image:
    rgb = to_rgb8(epoch.image, limits, None, diff) if epoch.planes == 3 else to_rgb8(epoch.image, limits[0], limits[1], diff)
    im = Image.fromarray(rgb).resize((size, size), Image.NEAREST)
    d = ImageDraw.Draw(im)
    f = _font(max(12, size // 32))
    c = size / 2
    if crosshair:
        g, L = size * 0.03, size * 0.06
        col = (255, 200, 0)
        for a, b in (((c - g - L, c), (c - g, c)), ((c + g, c), (c + g + L, c)), ((c, c - g - L), (c, c - g)), ((c, c + g), (c, c + g + L))):
            d.line([a, b], fill=col, width=2)
    if epoch.channels is not None:
        # an asterisk marks a channel taken from another date
        dets = " ".join(
            f"{c['color']}=D{c['detector']}{'*' if c['borrowed'] else ''}" for c in epoch.channels if c["detector"]
        )
    else:
        dets = ",".join(f"D{x}" for x in sorted({m.exposure.detector for m in epoch.members}))
    text = f"{epoch.label}   {dets}"
    fs = int(getattr(f, 'size', 12))
    d.rectangle([0, 0, size, fs + 10], fill=(0, 0, 0))
    d.text((6, 4), text, fill=(255, 255, 255), font=f)
    if title:
        d.text((6, size - fs - 8), title, fill=(255, 220, 120), font=f)
    small = _font(max(9, fs - 3))
    d.text((size - d.textlength("N up, E left", font=small) - 6, 4), "N up, E left", fill=(150, 190, 230), font=small)
    return im


def binned(epochs: list[Epoch], b: int) -> list[Epoch]:
    """Epochs with b x b pixels averaged (wide mosaics are sent to the browser
    at overview resolution; pixels without data do not count)."""
    if b <= 1 or not epochs:
        return epochs
    out = []
    for e in epochs:
        img = e.image if e.image.ndim == 3 else e.image[None]
        n = img.shape[-1] // b * b
        blocks = img[:, :n, :n].reshape(img.shape[0], n // b, b, n // b, b)
        with _quiet():
            small = np.nanmean(blocks, axis=(2, 4)).astype(np.float32)
        out.append(Epoch(e.label, e.mjd, small if e.image.ndim == 3 else small[0], e.members, e.channels))
    return out


def encode_cube(epochs: list[Epoch]) -> tuple[bytes, dict]:
    """Pack epoch images for the browser: uint16, asinh-compressed, 0 = no data.
    Order is [epoch, (channel R, G, B for colour,) row, column].

    value = sinh(a_lo + (q - 1) / 65534 * (a_hi - a_lo)), in units of the
    per-exposure sky noise (sigma).
    """
    if not epochs:
        return b"", {"a_lo": 0.0, "a_hi": 1.0, "n": 0, "planes": 1}
    cube = np.stack([e.image for e in epochs]).astype(np.float32)
    finite = np.isfinite(cube)
    a = np.arcsinh(np.where(finite, cube, 0.0))
    a_lo = float(a[finite].min()) if finite.any() else 0.0
    a_hi = float(a[finite].max()) if finite.any() else 1.0
    if a_hi <= a_lo:
        a_hi = a_lo + 1.0
    q = np.where(finite, 1 + np.round((a - a_lo) / (a_hi - a_lo) * 65534), 0).astype("<u2")
    return q.tobytes(), {"a_lo": a_lo, "a_hi": a_hi, "n": len(epochs), "planes": epochs[0].planes}


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
