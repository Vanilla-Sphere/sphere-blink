# Vanilla Sphere

**Watch any patch of the infrared sky change over time.**

Enter a position (RA/Dec or an object name) and Vanilla Sphere plays every
NASA SPHEREx image of that patch as a short movie, so moving and variable
objects stand out. Built by the team of the same name for the 2026 NASA Space
Apps Challenge, "Planet X and SPHEREx". Live at <https://vanillasphere.com>.

## Documentation

The product design documentation is in [`docs/`](docs/README.md): overview,
features, architecture, imaging engine, measurement pipeline, API reference,
viewer, code structure, data sources, performance and deployment.

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
  0.75-5.0 um spectrum, plus the flux over time with a line saying whether
  the object changed between visits.
- **A first reading of the spectrum.** Under the plot the viewer says what the
  curve shows: where it peaks, how it slopes, which bands and lines stand at
  their reference wavelengths (water vapour, methane, water and carbon dioxide
  ice, hydrogen and other atomic lines, PAH), and which kinds of object give
  such a curve. It is written by fixed rules in the browser
  (`web/insight.js`), labelled preliminary, and states its confidence and
  limits. The wavelengths come from a cited catalogue (`web/linelist.js`:
  NIST Atomic Spectra Database, published band origins of water and methane,
  the literature on ices and dust; every number read in its source),
  and for a galaxy or quasar with a published redshift the lines are looked
  for where the redshift puts them.
- **Bright stars too.** A star brighter than about K = 9 saturates the
  detector core and an aperture on it reads 2-5 times low. Those points are
  measured from the star's halo instead, with a calibration fitted to 81
  2MASS stars (`sphereblink/wings.py`, `scripts/calibrate_wings.py`): within
  20-25 % of 2MASS from 0.15 to 20 Jy, marked `method: "wings"`, and flagged
  `EXTRAPOLATED` beyond that range.
- **Overlays**: Gaia DR3 and CatWISE2020 sources, moved by their proper
  motion to the date of each frame; fast movers get a track and a label.
- **Quality flags** of the pixels under the target are shown for every frame.
- **Measures by itself.** For any object the server finds its position on the
  native detector pixels of every exposure (PSF fit), compares it with where
  Gaia says it should be on that date, and fits proper motion with parallax.
  Objects too faint for one exposure are added up along their predicted path,
  one stack per sky pass. Variable objects get a light curve that is
  independent of wavelength. The five featured targets are measured
  automatically; see [Measurements](#measurements).
- **Coordinates in the URL** and an image API, so any view can be linked or embedded:

  ```
  /?target=Barnard's Star&fov=8
  /?ra=133.76010&dec=-7.24227&fov=8&det=5,6&group=visit
  /api/movie.gif?target=barnard&fov=8&group=day
  /api/frame.png?ra=269.44629&dec=4.76792&fov=8&i=-1
  /api/spectrum?target=R Dra&wait=60
  /?ra=206.005&dec=-10.763&levels=3                 progressive field, 7 x 7 cells
  /api/tiles?ra=206.005&dec=-10.763&levels=3        its cells, stages and progress
  /api/measure?target=WISE 0855                     positions, fitted motion, light curve
  /api/measured                                     everything measured so far
  ```

  Interactive API documentation is served at `/docs`.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn sphereblink.main:app --port 8000
# open http://localhost:8000/?target=barnard
```

Run it as one web process (`--workers 1`, the default unless `WEB_CONCURRENCY`
is set): views in progress are kept in that process, which starts its own
worker processes for the cutting.

Settings are environment variables (see `sphereblink/config.py`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SPHEREX_COLLECTIONS` | `spherex_dr1,spherex_qr3` | IRSA collections to search, newest first. A new data release only needs its name added here. |
| `SPHEREX_CACHE_DIR` | `.cache/sphereblink` | Where the SQLite cutout cache lives (put it on a persistent disk). |
| `SPHEREX_FETCH_WORKERS` | `64` | Concurrent exposures being read. |
| `SPHEREX_MAX_FOV_ARCMIN` | `51` | Largest field of view. |
| `SPHEREX_AUTO_MEASURE` | `1` | Measure the five featured targets by itself after start (`0` = only on request). |
| `SPHEREX_WARM_CATALOGS` | `1` | Store the Gaia and CatWISE overlays of the five featured targets after start, so they show at once (`0` = only on request). |
| `DATABASE_URL` | empty | Postgres for measured positions and tracks (`sql/`). Without it they are kept on disk only. |

## How it works

```
browser ──> FastAPI ──> IRSA SIA v2            which exposures cover (RA, Dec)?
                   └──> s3://nasa-irsa-spherex  4 range requests in 3 steps:
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
| `sphereblink/measure.py` | native stamps, PSF fits, stacks for faint objects, track fit, light curve |
| `sphereblink/refcat.py` | catalogue astrometry of the targets (Gaia DR3 at ESA, IRSA as fallback) |
| `sphereblink/measure_api.py` | measurement jobs, `/api/measure`, `/api/measured` |
| `sphereblink/measurements.py` | stored results and the team database behind `/api/tracks` |
| `sphereblink/service.py` | background jobs and cache |
| `sphereblink/tiles.py` | fixed 0.12 degree cell grid, progressive fields |
| `sphereblink/main.py` | HTTP API |
| `web/` | the viewer (plain HTML/JS, no build step): `app.js` the views, `starmap.js` the sky map, `i18n.js` the interface text, `hints.js` the notes on the controls, `lore.js` the constellation and star notes on the map |
| `sql/` | optional shared Postgres schema for measured positions and tracks |

## Measurements

`GET /api/measure?target=...` (or `ra=&dec=`) starts a job and is polled until
`status` is `ready`. What the job does, in `sphereblink/measure.py`:

1. **Stamps.** For each exposure, a 49 x 81 pixel piece of the detector around
   the predicted position is read with range requests (about 0.4 MB), with
   its flags. No resampling: positions are measured on the pixels the
   detector recorded.
2. **Prediction.** The catalogue position is moved to the date of the
   exposure by proper motion and parallax (Earth ephemeris from Astropy).
3. **PSF fit.** A pixel-integrated Gaussian is fitted at the prediction; Gaia
   stars within 30 arcsec are fitted along with it so that a bright neighbour
   cannot capture the fit. A star whose core is saturated is centred on its
   wings instead, and its brightness is taken from a ring outside the core.
4. **Stacks.** Where fewer than half of the exposures of a sky pass show the
   object, the exposures of that pass and detector are added along the
   predicted path and the stack is measured. Its errors come from
   bootstrapping the exposures.
5. **Track.** Position, proper motion and (catalogue) parallax are fitted to
   all positions, with outlier rejection and jackknife errors over
   (sky pass, detector) groups. With three passes the parallax is also fitted.
6. **Light curve.** Every flux is divided by the object's mean spectrum at
   that wavelength, which makes exposures at different wavelengths comparable.

What it gives on the featured targets (DR1 + QR3, measured 2026-11-14):

| Target | Positions | Fitted proper motion (mas/yr) | Catalogue | Note |
| --- | --- | --- | --- | --- |
| Barnard's Star | 86 on detectors 5-6 (254 more from saturated wings, not used) | -992 ± 472, +10327 ± 249 | -801.6, +10362.4 (Gaia DR3) | position 0.1 arcsec from the Gaia prediction |
| Epsilon Indi B | 431 | +3945 ± 48, -2513 ± 49 | +3982.0, -2466.8 (Gaia DR3) | 0.6 arcsec from the Gaia prediction (unresolved pair Ba+Bb); brightness constant to 2 % |
| WISE 0855-0714 | 6, of which 5 are stacks of 14-25 exposures | -8766 ± 992, +1536 ± 992 | -8123.7, +673.2 (Kirkpatrick et al. 2021) | invisible in single exposures, found at 4-5 um in the stacks |
| RW Aurigae | 271 | +316 ± 288, +49 ± 84 | +4.9, -24.6 (Gaia DR3) | no change beyond the 12 % ring accuracy (0.99, 1.14, 1.00 in three passes); most exposures are saturated |
| R Draconis | 515, all from saturated wings | +154 ± 334, +241 ± 426 | -20.6, +6.1 (Gaia DR3) | faded by a factor 2.0 from Jun 2025 to Jun 2026 (Mira variable) |

Honest limits: SPHEREx pixels are 6.15 arcsec and the PSF is undersampled, so
one exposure gives 0.2-0.5 arcsec at best; with three or four sky passes
spread over little more than a year the proper motion errors are tens to
hundreds of mas/yr. The spacecraft is turned around between passes, which
leaves a 0.1-0.3 arcsec offset that changes sign each pass and limits the
free parallax fit. Ring fluxes of saturated stars are good to about 12 %.
A PSF fit to what is left of a saturated core reads 2-5 times low, so such
exposures give a position but no core flux; a star saturated in most of its
exposures (RW Aurigae, Barnard's Star, R Draconis) gets its whole light curve
from the ring.
A position where nothing is found gives no track, and neither does one
where the only source found lies more than 3 arcsec (half a pixel) from the
place asked for: that is a neighbour, reported as `stats.nearby_source` with
its positions marked unused. Such a result is not written to the database.
The pipeline only writes rows with `method` `psf`, `wings` or `stack`;
measurements loaded by hand under another method name are left alone.

## Data and credits

- NASA SPHEREx Level 2 spectral images (DR1, QR3), NASA/IPAC Infrared Science
  Archive, read from the AWS Open Data mirror `nasa-irsa-spherex`.
- ESA Gaia DR3 (ESA Gaia Archive and IRSA) and CatWISE2020 (IRSA) catalogues.
- WISE 0855-0714 astrometry: AllWISE position; Kirkpatrick et al. 2021 (ApJS 253, 7) motion and parallax.
- Astropy, NumPy, SciPy, FastAPI, Pillow.
- Interface: the "Rim" theme of [Hallmark](https://github.com/nutlope/hallmark) (MIT), laid out by the
  tool-interface rules of [Impeccable](https://github.com/pbakaus/impeccable). Typefaces Hanken Grotesk and
  Geist Mono, SIL Open Font License (`web/fonts/`).
- Sky map: bright stars, constellation figures and the Milky Way outline from
  [d3-celestial](https://github.com/ofrohn/d3-celestial) by Olaf Frohn (BSD 3-Clause, notice in
  `web/data/`), reduced to `web/data/sky.json` by `scripts/build_sky.py`. The photograph behind the
  zoomed-in map is the 2MASS near-infrared atlas (University of Massachusetts and IPAC/Caltech, funded by
  NASA and NSF), in HiPS format by CDS, served by the NASA/IPAC Infrared Science Archive. It is a backdrop,
  not SPHEREx data.

## Use of AI tools

This project was built during the 2026 NASA Space Apps Challenge with the help
of an AI assistant, Claude (Anthropic), used through Claude Code. In line with
the challenge rules on Super Intelligence (SI) tools:

- **Code.** Most of the source code and tests in this repository were written
  by Claude under the team's direction and review.
- **Research and checks.** Claude helped work out the SPHEREx data access path
  (IRSA SIA, range reads from the AWS Open Data mirror) and ran the timing and
  pixel-comparison checks.
- **Interface and text.** Claude helped design the viewer and draft this README
  and the submission text.
- **Not AI.** No AI or machine-learning model runs inside the product. Every
  image shown is NASA SPHEREx data processed by deterministic code, and no
  image, video or audio in the project is AI-generated.

## License

Vanilla Sphere. Copyright (C) 2026 Vanilla Sphere.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
details.

The full text is in [`LICENSE`](LICENSE); the SPDX identifier is
`AGPL-3.0-only`. Under section 13 of that licence, whoever modifies this
program and lets others use the modified version over a network must offer
those users the source code of that version.

Not covered by the AGPL; each keeps its own terms, listed in
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md):

- The typefaces in `web/fonts/` (SIL Open Font License 1.1).
- `web/data/sky.json`, derived from d3-celestial (BSD 3-Clause).
- Data taken from catalogues and papers: `web/data/redshift.json` (SIMBAD),
  `web/data/quasar-template.json` (VizieR), the reference wavelengths in
  `web/linelist.js` (NIST ASD and the papers cited there) and the recorded
  catalogue answers in `tests/data/`. They stay under the terms of their
  sources, credited in [docs/data-sources.md](docs/data-sources.md).
- The name "Vanilla Sphere" and its logo (`web/logo*.svg`, `web/favicon.svg`,
  `web/apple-touch-icon.png`, `web/og.png`). The licence grants no right to
  use them (section 7(e)), other than to say where the code comes from.
- The NASA SPHEREx images and the catalogue data the service shows. They are
  not part of this repository; their credits are under "Data and credits".
