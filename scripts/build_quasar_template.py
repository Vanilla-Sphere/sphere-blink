"""Builds web/data/quasar-template.json: one mean quasar spectrum from 0.1 to 3.5 um at rest.

Two published composites, both read from VizieR (CDS):

- Selsing, Fynbo, Christensen & Krogager 2016, A&A 585, A87 (J/A+A/585/A87): seven bright quasars at
  z = 1.1 to 2.1 observed with X-shooter, 0.1 to 1.13 um at rest. Used below 0.55 um.
- Glikman, Helfand & White 2006, ApJ 640, 579 (J/ApJ/640/579, table 7): 27 quasars at z = 0.1 to 0.4,
  SDSS spectra joined to IRTF SpeX spectra, 0.28 to 3.52 um at rest. Used above 0.60 um.

Between 0.55 and 0.60 um, a stretch without strong lines, the two are blended; the Glikman spectrum is
first brought to the level of the Selsing one there. The result is in F_nu, on a grid of equal steps
in ln(wavelength) (1/800), with the level at 0.68 um set to 1.

web/redshift.js compares a SPHEREx spectrum with this template at every trial redshift.

    python scripts/build_quasar_template.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "web" / "data" / "quasar-template.json"
MIRRORS = ("https://vizier.cfa.harvard.edu", "https://vizier.cds.unistra.fr", "https://vizier.idia.ac.za")
SELSING = ("J/A+A/585/A87/spectrum", "lambda,S")
GLIKMAN = ("J/ApJ/640/579/table7", "Lambda,Flux")
JOIN = (0.55, 0.60)          # um, where one composite hands over to the other
W0, W1, STEP = 0.10, 3.52, 1 / 800


def fetch(source: str, columns: str) -> np.ndarray:
    """Wavelength (um) and flux (F_lambda, arbitrary) of one VizieR table; rows without flux are left out."""
    last = None
    for host in MIRRORS:
        try:
            r = httpx.get(f"{host}/viz-bin/asu-tsv", timeout=120,
                          params={"-source": source, "-out.max": "unlimited", "-out": columns})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            last = exc
            continue
        rows = []
        for line in r.text.splitlines():
            parts = line.split("\t")
            if line.startswith("#") or len(parts) < 2:
                continue
            try:
                w, f = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if f > 0:
                rows.append((w / 1e4, f))
        if len(rows) > 1000:
            return np.array(rows)
        last = RuntimeError(f"{host} gave {len(rows)} rows of {source}")
    raise SystemExit(f"no VizieR mirror answered for {source}: {last}")


def on_grid(table: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Mean of the table in each cell of the grid; NaN where the table has nothing."""
    edges = np.exp(np.log(grid[0]) + (np.arange(len(grid) + 1) - 0.5) * STEP)
    cell = np.digitize(table[:, 0], edges) - 1
    ok = (cell >= 0) & (cell < len(grid))
    total = np.bincount(cell[ok], weights=table[ok, 1], minlength=len(grid))
    count = np.bincount(cell[ok], minlength=len(grid))
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def build() -> dict:
    grid = np.exp(np.log(W0) + np.arange(int(np.log(W1 / W0) / STEP) + 1) * STEP)
    blue, red = on_grid(fetch(*SELSING), grid), on_grid(fetch(*GLIKMAN), grid)
    join = (grid >= JOIN[0]) & (grid < JOIN[1])
    red = red * np.nanmedian(blue[join]) / np.nanmedian(red[join])
    share = np.clip((grid - JOIN[0]) / (JOIN[1] - JOIN[0]), 0, 1)      # 0: Selsing, 1: Glikman
    flux = np.where(share <= 0, blue, np.where(share >= 1, red, (1 - share) * blue + share * red))
    flux = np.where(np.isfinite(flux), flux, np.where(share < 0.5, red, blue))
    good = np.isfinite(flux)
    if good.sum() < 0.98 * len(grid):
        raise SystemExit(f"the composites leave {len(grid) - good.sum()} of {len(grid)} cells empty")
    flux = np.interp(np.log(grid), np.log(grid[good]), flux[good])
    flux = flux * grid**2                                                 # F_lambda to F_nu
    flux = flux / np.median(flux[(grid > 0.67) & (grid < 0.69)])
    return {
        "made": time.strftime("%Y-%m-%d", time.gmtime()),
        "source": "Selsing et al. 2016, A&A 585, A87 (below 0.55 um); Glikman, Helfand & White 2006, ApJ 640, 579 "
                  "(above 0.60 um); both from VizieR (CDS)",
        "units": "F_nu, 1 at 0.68 um; wavelength at rest in um",
        "wave0": W0,
        "step": STEP,
        "flux": [float(f"{v:.4g}") for v in flux],
    }


if __name__ == "__main__":
    made = build()
    OUT.write_text(json.dumps(made, separators=(",", ":")))
    print(f"{OUT.name}: {len(made['flux'])} points, {OUT.stat().st_size / 1024:.0f} KB", file=sys.stderr)
