# SPHEREx Blink

**Watch any patch of the infrared sky change over time.**

Enter a position (RA/Dec or an object name) and SPHEREx Blink plays every
NASA SPHEREx image of that patch as a short movie, so moving and variable
objects stand out. Built by team Vanilla Sphere for the 2026 NASA Space Apps
Challenge, "Planet X and SPHEREx".

## What it does

- **Any coordinates, on demand.** Nothing is pre-downloaded. The server finds
  every SPHEREx Level 2 exposure covering the position (IRSA SIA) and reads
  only the bytes it needs from the public AWS Open Data mirror with HTTP range
  requests: 1.3-1.9 MB per exposure for an 8 arcmin view instead of the 65 MB
  file, pixel for pixel identical to a cut from the full download.
- **Progressive fields, 7 x 7 cells.** The sky is addressed by the fixed
  0.12 degree cells of the team's measurement grid (`icrs012-v1-XXXX-YYYY`).
  A field starts at the cell of the requested position and grows ring by
  ring: the cell is on screen after seconds, its 3 x 3 neighbourhood next,
  then the whole 7 x 7 block (0.84 degrees, about 700 exposures) as one
  false-colour mosaic. Each exposure is read once for the whole block.
- **Blink movie** with a shared contrast stretch, time slider, keyboard
  control, and a timeline of the real observation dates.
- **Six detector bands** (0.75-5.0 um). Frames can be stacked per UTC day or
  per six-month sky pass to raise signal-to-noise, or shown exposure by exposure.
- **Image math**: subtract the median of all frames (static stars vanish),
  subtract a chosen reference epoch, or add everything together.
- **Spectrum on the fly.** Each exposure sees the target through a different
  slice of the linear variable filter, so the exposures together give a
  0.75-5.0 um spectrum, plus a light curve per detector.
- **Overlays**: Gaia DR3 and CatWISE2020 sources, moved by their proper
  motion to the date of each frame; fast movers get a track and a label.
- **Quality flags** of the pixels under the target are shown for every frame.
- **Coordinates in the URL** and an image API, so any view can be linked or embedded:

  ```
  /?target=Barnard's Star&fov=8
  /?ra=133.76808&dec=-7.24292&fov=8&det=5,6&group=visit
  /api/movie.gif?target=barnard&fov=8&group=day
  /api/frame.png?ra=269.44629&dec=4.76792&fov=8&i=-1
  /api/spectrum?target=R Dra&wait=60
  /?ra=206.005&dec=-10.763&levels=3                 progressive field, 7 x 7 cells
  /api/tiles?ra=206.005&dec=-10.763&levels=3        its cells, stages and progress
  ```

  Interactive API documentation is served at `/docs`.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn sphereblink.main:app --port 8000
# open http://localhost:8000/?target=barnard
```

Settings are environment variables (see `sphereblink/config.py`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SPHEREX_COLLECTIONS` | `spherex_dr1,spherex_qr3` | IRSA collections to search, newest first. A new data release only needs its name added here. |
| `SPHEREX_CACHE_DIR` | `.cache/sphereblink` | Where the SQLite cutout cache lives (put it on a persistent disk). |
| `SPHEREX_FETCH_WORKERS` | `32` | Concurrent exposures being read. |
| `SPHEREX_MAX_FOV_ARCMIN` | `30` | Largest field of view. |

## How it works

```
browser ──> FastAPI ──> IRSA SIA v2            which exposures cover (RA, Dec)?
                   └──> s3://nasa-irsa-spherex  3 range requests per exposure:
                          1. IMAGE header (WCS)
                          2. the pixel rows of the cutout + FLAGS row index
                          3. the RICE-compressed FLAGS rows
            mask bad pixels -> subtract sky -> resample to a north-up grid
            -> SQLite cache -> epochs (day / sky pass / exposure) -> browser
```

| Module | Role |
| --- | --- |
| `sphereblink/sia.py` | exposure search |
| `sphereblink/cutout.py` | range-request FITS reader, masking, resampling, aperture photometry |
| `sphereblink/render.py` | epoch stacking, shared stretch, PNG / GIF / binary cube |
| `sphereblink/resolve.py` | coordinates and names, proper-motion propagation |
| `sphereblink/catalogs.py` | Gaia DR3 / CatWISE2020 overlays through IRSA TAP |
| `sphereblink/service.py` | background jobs and cache |
| `sphereblink/tiles.py` | fixed 0.12 degree cell grid, progressive fields |
| `sphereblink/main.py` | HTTP API |
| `web/` | the viewer (plain HTML/JS, no build step) |
| `sql/` | optional shared Postgres schema for measured positions and tracks |

## Data and credits

- NASA SPHEREx Level 2 spectral images (DR1, QR3), NASA/IPAC Infrared Science
  Archive, read from the AWS Open Data mirror `nasa-irsa-spherex`.
- ESA Gaia DR3 and CatWISE2020 catalogues, queried through IRSA TAP.
- Astropy, NumPy, SciPy, FastAPI, Pillow.
- Interface: the "Rim" theme of [Hallmark](https://github.com/nutlope/hallmark) (MIT), laid out by the
  tool-interface rules of [Impeccable](https://github.com/pbakaus/impeccable). Typefaces Hanken Grotesk and
  Geist Mono, SIL Open Font License (`web/fonts/`).

## License

MIT
