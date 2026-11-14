# Code structure

## Repository layout

```
sphere-blink/
├── sphereblink/            Python package: the service
│   ├── __init__.py         pins numeric libraries to one thread per process
│   ├── config.py           settings from environment variables
│   ├── sia.py              exposure search (IRSA SIA v2)
│   ├── cutout.py           FITS range reader, masking, resampling, aperture photometry
│   ├── worker.py           tasks that run in the imaging worker processes
│   ├── service.py          SQLite cache, view jobs, process pool
│   ├── tiles.py            fixed 0.12 degree cell grid, progressive fields
│   ├── render.py           epochs, false colour, stretch, PNG, GIF, binary cube
│   ├── resolve.py          coordinates and object names to positions
│   ├── catalogs.py         Gaia DR3 and CatWISE2020 overlays (IRSA TAP)
│   ├── wings.py            flux of saturated stars from their halo
│   ├── measure.py          native stamps, PSF fits, stacks, track fit, light curve
│   ├── refcat.py           reference astrometry for the measurement pipeline
│   ├── measure_api.py      measurement jobs and endpoints
│   ├── measurements.py     result stores: local SQLite and team Postgres
│   └── main.py             FastAPI application: endpoints and static files
├── web/                    the viewer (static; see viewer.md)
├── sql/                    schema and roles of the measurement database
├── scripts/                maintenance and calibration tools
├── tests/                  unit tests (no network)
├── docs/                   this documentation
├── render.yaml             hosting blueprint
├── requirements.txt        Python dependencies
├── README.md
└── LICENSE                 MIT
```

About 4 300 lines of Python in the package, 850 lines of tests, and 2 100 lines
of HTML, CSS and JavaScript in the viewer.

## Modules

| Module | Lines | Responsibility | Main names |
| --- | --- | --- | --- |
| `config.py` | 59 | All tunable settings, read once from the environment | `COLLECTIONS`, `S3_HTTP_BASE`, `CACHE_DIR`, `MAX_FOV_ARCMIN`, `FETCH_PROCS`, `BAD_FLAG_NAMES` |
| `sia.py` | 175 | Query the archive for exposures covering one or many points; parse CSV or VOTable | `Exposure`, `find_exposures_at`, `parse_sia_csv` |
| `cutout.py` | 455 | The view grid; four-request FITS reader; Rice decoding of flags; masking; sky plane; resampling; aperture flux; wavelength maps | `Grid`, `Frame`, `cut_async`, `detector_coords`, `aperture_flux`, `wave_map_from_file`, `cache_key` |
| `worker.py` | 77 | Code executed inside worker processes: per-process event loop and HTTP client, chunk cutting with retries, traffic metering | `init`, `cut_chunk`, `cut_one`, `Meter` |
| `service.py` | 408 | SQLite store; job lifecycle; exposure search with locks and stale fallback; process pool; memory and disk budgets | `Store`, `Job`, `Service` |
| `tiles.py` | 202 | Cell arithmetic and identifiers; block and stage geometry; field lifecycle | `cell_of`, `tile_id`, `block`, `stage_grid`, `Field`, `Fields` |
| `render.py` | 393 | Grouping into epochs; colour composition with provenance; shared limits; asinh rendering; binning; cube, PNG and GIF encoding | `Epoch`, `build_epochs`, `build_color_epochs`, `difference`, `shared_limits`, `encode_cube` |
| `resolve.py` | 277 | Parse coordinates; featured targets and aliases; Sesame and SIMBAD lookups with mirrors; proper-motion propagation | `Target`, `resolve`, `FEATURED`, `NameNotFound`, `ResolverUnavailable` |
| `catalogs.py` | 78 | ADQL cone queries for the overlays; unit and epoch normalisation; motion significance | `query` |
| `wings.py` | 134 | Ring levels around a saturated star; calibration table; flux with systematic error | `ring_levels`, `measure`, `CALIBRATION` |
| `measure.py` | 1070 | The measurement algorithms; no HTTP endpoints, no database | `Body`, `Stamp`, `Detection`, `fit_psf`, `fit_wings`, `measure_stack`, `fit_track`, `light_curve`, `measure_all`, `fetch_stamps` |
| `refcat.py` | 117 | Catalogue astrometry of the featured targets; Gaia cone query with fallback; matching a request to a catalogue object | `TARGETS`, `gaia_cone`, `body_for` |
| `measure_api.py` | 204 | Measurement job runner, automatic measurement of the featured targets, the two measurement endpoints | `router`, `start`, `run`, `VERSION` |
| `measurements.py` | 280 | Local result store; Postgres reader and writer; merged cone search | `save_local`, `save_db`, `sync_db`, `near` |
| `main.py` | 397 | Application object, middleware, parameter handling, all other endpoints, epoch cache, static mount | `app`, `svc`, `fields` |

## Dependency rules

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/diagrams/code-layers-dark.png">
  <img alt="Layer stack showing the HTTP layer on top of orchestration, the numerical core, external data access and settings; each layer imports only from layers below it." src="img/diagrams/code-layers.png">
</picture>

*Figure: Dependency layers of the package. Source: [`diagrams/code-layers.html`](diagrams/code-layers.html).*

- Only `main.py` and `measure_api.py` import FastAPI.
- `cutout`, `render`, `wings`, `tiles` and `measure` are pure functions and data
  classes over NumPy arrays; they can be used from a script or a notebook.
  `measure.measure_all` returns a plain dictionary.
- `measure.py` reuses the byte-level FITS helpers of `cutout.py` rather than
  duplicating them.
- Only `measurements.py` talks to Postgres, and it imports the driver lazily so
  the service runs without it when no database is configured.
- State that must be shared (jobs, fields, caches) is held by the single
  `Service` and `Fields` instances created in `main.py`.

## Core data types

| Type | Module | Meaning |
| --- | --- | --- |
| `Exposure` | `sia` | One Level 2 file: identifiers, time range, wavelength range, S3 key, footprint |
| `Grid` | `cutout` | The image grid of a view; its `key()` identifies the view |
| `Frame` | `cutout` | One exposure on a grid, with wavelength, aperture flux and flags at the target |
| `Job` | `service` | The gathering of a view's frames: status, counters, statistics, frames so far |
| `Field` | `tiles` | A progressive field: seed cell, cells, stages |
| `Epoch` | `render` | One displayed image, its members and (for colour) channel provenance |
| `Target` | `resolve` | A resolved request: position at the SPHEREx epoch, catalogue position, motion, source |
| `Body` | `measure` | A catalogue object with position, epoch, proper motion and parallax; predicts positions |
| `Stamp` | `measure` | Native detector pixels around a position in one exposure, with WCS and flags |
| `Fit` | `measure` | Result of a PSF or wing fit in pixel coordinates |
| `Detection` | `measure` | A measured position and flux in one exposure or stack |

## Scripts

| Script | Purpose | Usage |
| --- | --- | --- |
| `scripts/prewarm.py` | Fill the cache of a running server for the featured targets and optional coordinate lists | `python scripts/prewarm.py https://server [--fov 4,8,15] [coords.txt]` |
| `scripts/calibrate_wings.py` | Measure how halo brightness scales with flux on 2MASS calibrators and print the table for `wings.CALIBRATION` | `collect` then `fit`, with `scripts/wing_calibrators.csv` |
| `scripts/wing_calibrators.csv` | The 81 isolated 2MASS stars used for the calibration | - |
| `scripts/build_sky.py` | Build `web/data/sky.json` from d3-celestial data | `python scripts/build_sky.py path/to/d3-celestial/data` |

## Database files

| File | Content |
| --- | --- |
| `sql/schema.sql` | Tables `sources`, `detections`, `tracks`; indexes; function `sources_near`; row level security; pipeline columns |
| `sql/roles.sql` | Login roles `blink_loader` (read and write), `blink_web` (read only), `blink_service` (the web service) and their policies |
| `sql/example_insert.sql` | Example upserts for loading measurements by hand |
| `sql/README.md` | How to load data |

## Tests

48 unit tests in three files, run with `pytest`. They use synthetic data and
stubbed HTTP clients, need no network, and finish in about three seconds.

| File | Tests | Covers |
| --- | --- | --- |
| `tests/test_offline.py` | 22 | Coordinate parsing; proper-motion propagation of the featured targets; grid geometry; FITS header scanning; aperture photometry; sky plane; epoch grouping; median difference; cube round trip; PNG and GIF rendering; colour borrowing and white balance; disk and memory eviction; cell grid and stages; exactness of the coarse-lattice pixel mapping; binning; half-precision storage; SIA CSV parsing; wing photometry |
| `tests/test_measure.py` | 14 | Parallax factors; PSF fit accuracy; neighbour handling; wing fallback; track recovery with parallax; stacks for faint movers; light curve; storage and database synchronisation; saturated stars |
| `tests/test_resolve_names.py` | 12 | Name resolution paths: Sesame, mirrors, redirects, SIMBAD fallback, spellings, "not found" against "unavailable", no network for built-in targets |

```bash
pip install -r requirements.txt pytest
SPHEREX_AUTO_MEASURE=0 pytest -q
```

Numerical claims that matter for correctness are tests rather than comments: for
example that the coarse-lattice mapping is exact to 1/1000 pixel, that ignoring
parallax biases the fitted motion, and that half-precision storage preserves the
image within the noise.

## Dependencies

| Package | Used for |
| --- | --- |
| `fastapi`, `uvicorn[standard]` | HTTP service |
| `astropy` | FITS headers, WCS with SIP distortion, Rice codec, time and ephemeris, coordinate parsing |
| `numpy`, `scipy` | Array arithmetic; interpolation, labelling, least squares, error function |
| `httpx` | HTTP client, synchronous and asynchronous, for range requests and services |
| `fsspec`, `aiohttp` | Remote FITS access for the one-time wavelength-map read |
| `pillow` | PNG and GIF encoding, annotation |
| `psycopg[binary]` | Postgres driver (only when a database is configured) |

The viewer has no runtime dependencies.

## Conventions

- **Python.** Type hints throughout; `from __future__ import annotations`; data
  classes for records; module docstrings state the design intent. Functions that
  are not part of a module's interface start with an underscore.
- **Comments explain why.** In-code comments record the reason for a choice and,
  where relevant, the measurement behind it.
- **Units in names.** `_ujy`, `_um`, `_arcsec`, `_deg`, `_mjd`, `_masyr`, `_s`.
- **No hidden globals.** Settings come from `config.py`; mutable service state
  belongs to `Service`, `Fields` or the module-level job table of `measure_api`.
- **Cache keys are versioned.** `cutout.cache_key` and the catalogue cache key
  include a version tag; `measure_api.VERSION` versions measurement results.
  Raise the tag when the stored format or the method changes.
- **Branches and review.** Work happens on a branch and reaches `main` through a
  pull request; `main` deploys automatically.
- **Parallel workstreams.** The imaging engine, the viewer, the measurement
  pipeline and the documentation are developed in parallel; changes to shared
  files (`main.py`, `web/app.js`) start from the latest `main`.

## Where to make common changes

| Change | Place |
| --- | --- |
| Support a new SPHEREx data release | `SPHEREX_COLLECTIONS` (no code change) |
| Add an endpoint | `main.py`; reuse `_view`, `_frames`, `_epochs` |
| Change masking, background or resampling | `cutout.cut_async`; raise the version tag in `cache_key` |
| Change colour mapping | `render.RGB_CHANNELS`, `build_color_epochs`; mirror the stretch in `web/app.js` `draw` |
| Add an overlay catalogue | `catalogs.QUERIES`; a checkbox and a style in the viewer |
| Change the measurement method | `measure.py`; raise `measure_api.VERSION` |
| Add a built-in target | `resolve.FEATURED` and `ALIASES`; `refcat.TARGETS`; text in `web/i18n.js` |
| Add a name-service mirror | `resolve.SESAME_URLS`, `SIMBAD_TAP_URLS` |
| Add interface text | `web/i18n.js` (`STR.en`) |
