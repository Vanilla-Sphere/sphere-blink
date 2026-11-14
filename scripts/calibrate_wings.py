"""Measure how the halo of a star scales with its flux (see sphereblink/wings.py).

    python scripts/calibrate_wings.py collect scripts/wing_calibrators.csv samples.csv
    python scripts/calibrate_wings.py fit scripts/wing_calibrators.csv samples.csv

``wing_calibrators.csv`` lists isolated 2MASS stars (ra, dec, J, H, Ks; K of
3 to 11) picked from eight fields across the sky. ``collect`` opens an
8 arcmin view on each and writes one line per exposure: aperture flux, ring
levels and whether the core is saturated (about 15 s and 0.5 GB of archive
reads per star). ``fit`` prints the table for ``wings.CALIBRATION``, the
trend for ``wings._excess`` and how the result compares with 2MASS.
"""

from __future__ import annotations

import csv
import json
import sys

import numpy as np

ZERO_JY = {"j": (1.235, 1594.0), "h": (1.662, 1024.0), "k": (2.159, 666.7)}  # 2MASS: wavelength, zero point


def collect(stars_path: str, out_path: str) -> None:
    from sphereblink import wings
    from sphereblink.cutout import Grid
    from sphereblink.service import Service

    svc = Service()
    with open(stars_path, encoding="utf-8") as fh:
        stars = [(float(r["ra"]), float(r["dec"])) for r in csv.DictReader(fh)]
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["star", "uid", "det", "wave_um", "flux_ujy", "flux_err_ujy", "saturated"]
                   + [f"{k}{i}" for i in range(len(wings.RINGS)) for k in ("level", "err", "frac")])
        for n, (ra, dec) in enumerate(stars):
            grid = Grid.from_fov(ra, dec, 8.0)
            frames = svc.frames_blocking(grid)
            for f in frames:
                sat = any(x in f.flags_at_target for x in wings.SATURATION_FLAGS)
                levels = wings.ring_levels(f.image, grid.scale_arcsec, sat)
                w.writerow([n, f.exposure.uid, f.exposure.detector, f"{f.wave_um:.4f}", f"{f.flux_ujy:.1f}",
                            f"{f.flux_err_ujy:.1f}", int(sat)] + [f"{v:.5g}" for lv in levels for v in lv])
            print(f"star {n} {ra:.5f} {dec:.5f}: {len(frames)} exposures", flush=True)
            fh.flush()


def _line(x, y, s):
    """Weighted straight line y = a + b x."""
    w = 1 / s**2
    sw, sx, sy, sxx, sxy = w.sum(), (w * x).sum(), (w * y).sum(), (w * x * x).sum(), (w * x * y).sum()
    b = (sw * sxy - sx * sy) / (sw * sxx - sx * sx)
    return b, (sy - b * sx) / sw


def _slope(x, y, s):
    b, a = _line(x, y, s)
    keep = np.abs((y - a - b * x) / s) < 5
    return _line(x[keep], y[keep], s[keep])


def fit(stars_path: str, samples_path: str) -> None:
    with open(stars_path, encoding="utf-8") as fh:
        stars = list(csv.DictReader(fh))
    with open(samples_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    num = lambda r, k: float(r[k]) if r[k] not in ("nan", "") else np.nan  # noqa: E731
    star = np.array([int(r["star"]) for r in rows])
    wave = np.array([num(r, "wave_um") for r in rows])
    flux = np.array([num(r, "flux_ujy") for r in rows])
    ferr = np.array([num(r, "flux_err_ujy") for r in rows])
    sat = np.array([int(r["saturated"]) for r in rows])
    level = np.array([[num(r, f"level{i}") for i in range(3)] for r in rows])
    lerr = np.array([[num(r, f"err{i}") for i in range(3)] for r in rows])

    # 1. Innermost ring against the aperture flux of unsaturated stars, in
    #    16 wavelength bins, smoothed with a cubic in log wavelength.
    edges = np.exp(np.linspace(np.log(0.74), np.log(5.05), 17))
    mid = np.sqrt(edges[:-1] * edges[1:])
    clean = np.isfinite(flux) & (sat == 0) & (flux / ferr > 10) & np.isfinite(wave)
    factor, offsets = [], [[], [], []]
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = clean & (wave >= lo) & (wave < hi) & np.isfinite(level[:, 0]) & (lerr[:, 0] > 0)
        b, a = _slope(flux[m], level[m, 0], lerr[m, 0])
        factor.append(1 / b)
        offsets[0].append(a)
    smooth = np.exp(np.polyval(np.polyfit(np.log(mid), np.log(factor), 3), np.log(mid)))
    table = {"wave_um": [round(float(v), 4) for v in mid], "ring0": [int(v) for v in smooth]}
    # 2. Outer rings against the innermost one, all stars.
    for ring in (1, 2):
        ratio = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = ((wave >= lo) & (wave < hi) & np.isfinite(level[:, 0]) & np.isfinite(level[:, ring])
                 & (lerr[:, ring] > 0) & (level[:, 0] / lerr[:, 0] > 5))
            b, a = _slope(level[m, 0], level[m, ring], lerr[m, ring])
            ratio.append(b)
            offsets[ring].append(a)
        table[f"ring{ring}"] = [int(v) for v in smooth / np.array(ratio)]
    table["offset"] = [float(f"{np.median(o):.3g}") for o in offsets]
    print("CALIBRATION =", json.dumps(table))

    # 3. Plain wing flux of the middle ring against 2MASS: the trend with brightness.
    log_wave = np.log(table["wave_um"])
    x, y = [], []
    for n, s in enumerate(stars):
        for i, (band, (lam, zero)) in enumerate(ZERO_JY.items()):
            if not s.get(band) or (s.get("ph_qual") and s["ph_qual"][i] not in "AB"):
                continue
            m = (star == n) & (np.abs(wave - lam) < 0.1) & np.isfinite(level[:, 1])
            if m.sum() < 5:
                continue
            f1 = np.exp(np.interp(np.log(wave[m]), log_wave, np.log(table["ring1"])))
            plain = float(np.median(f1 * (level[m, 1] - table["offset"][1]))) / 1e6
            x.append(plain)
            y.append(plain / (zero * 10 ** (-float(s[band]) / 2.5)))
    x, y = np.array(x), np.array(y)
    m = (x > 0.8) & (y > 0.3) & (y < 5)
    b, a = np.polyfit(np.log10(x[m]), y[m], 1)
    for _ in range(4):
        r = y[m] - (a + b * np.log10(x[m]))
        keep = np.abs(r) < 2.5 * 1.4826 * np.median(np.abs(r - np.median(r)))
        b, a = np.polyfit(np.log10(x[m][keep]), y[m][keep], 1)
    faint = float(np.median(y[(x > 0.15) & (x <= 0.8)]))
    print(f"_excess(x) = max({faint:.2f}, {a:.3f} + {b:.3f} * log10(x))   [x in Jy, {m.sum()} star-bands above 0.8 Jy]")
    corrected = y / np.maximum(faint, a + b * np.log10(np.maximum(x, 1e-3)))
    true = x / y
    for lo, hi in ((0.15, 0.5), (0.5, 1.5), (1.5, 4), (4, 9), (9, 20)):
        k = (true >= lo) & (true < hi)
        if k.sum() > 2:
            med = np.median(corrected[k])
            print(f"  2MASS {lo:g}-{hi:g} Jy: {k.sum()} star-bands, wing / 2MASS {med:.2f}, "
                  f"scatter {100 * 1.4826 * np.median(np.abs(corrected[k] - med)):.0f} %")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "collect":
        collect(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "fit":
        fit(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
