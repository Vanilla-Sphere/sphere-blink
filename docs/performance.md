# Performance

All figures in this document were measured on 14 November 2026. Each table
states where. "Production" is the deployed service: Render web service, Pro Plus
plan (4 CPU, 8 GB), region Virginia, 20 GB persistent disk. "Cold" means nothing
for that view was in the cache; "warm" means the frames were on disk or in memory.

Timings depend on the archive's response times and vary between runs; ranges
are given where several runs were made.

## Headline figures

| Operation | Time | Data read from the archive |
| --- | --- | --- |
| 8 arcmin view, cold | about 10 s | about 350 exposures, 0.5 GB |
| 8 arcmin view, warm | under 1 s | none |
| 7 x 7 cell field (0.84 degrees), cold | 37-52 s; centre cell on screen after 14-20 s | 580-735 exposures, 5.9-7.2 GB |
| 7 x 7 cell field, warm | 4.6 s | none |
| Measurement of one object | 18-45 s | 258-539 stamps of about 0.4 MB |

## Range reads against whole files

| | Whole file | Range reads, 8 arcmin view |
| --- | --- | --- |
| Bytes per exposure | about 65 MB | 1.3-1.9 MB |
| HTTP requests per exposure | 1 | 4 |
| Bytes for a 350-exposure view | about 23 GB | about 0.5 GB |
| Pixel values | reference | identical |

The reduction is a factor of 35 to 50 in bytes. The comparison with a cut from
the fully downloaded file was made pixel by pixel and showed no difference.

## Views (production, cold)

| View | Exposures | Wall time | Notes |
| --- | --- | --- | --- |
| 8 arcmin | about 350 | about 10 s | 41 MB added to the disk cache |
| Seed cell of a field (7.2 arcmin) | - | 14-20 s | About 8 s of this is the archive search |
| 3 x 3 cells (21.6 arcmin) | - | 28 s | Cumulative from the start of the field |
| 7 x 7 cells (50.4 arcmin) | 580-735 | 37-52 s | Cumulative; 5.9-7.2 GB read at 85-220 MB/s |
| Two 7 x 7 fields started together | - | 54 s and 77 s | Sharing the four worker processes |

Per exposure, cutting costs about 40 ms of CPU (Rice decoding, WCS, resampling)
in addition to the network waits.

## Responses from a warm cache (production)

Single requests from a cloud client to the production service for Barnard's
Star at 8 arcmin (358 exposures, 39 daily epochs), with the view already cached:

| Request | Time | Size |
| --- | --- | --- |
| `/api/health` | 0.35 s | under 1 kB |
| `/api/view` | 0.13 s | 1 kB |
| `/api/cube` (false colour, by day; 39 epochs x 3 planes x 157 x 157) | 0.56 s | 3.9 MB compressed (5.8 MB of pixels) |
| `/api/frame.png` | 0.28 s | 27 kB |
| `/api/movie.gif` (39 frames) | 0.93 s | 1.1 MB |
| `/api/tracks` | 0.22 s | - |
| `/api/catalog` (cached) | 0.17 s | - |

These are single samples and include the network round trip of the client. The
2 x 2 binned colour cube of a 7 x 7 field is 11.7 MB.

## Measurements (production)

Run time of the measurement job as recorded in `stats.seconds`:

| Object | Exposures | Detections | Time |
| --- | --- | --- | --- |
| Wolf 359 | 290 | 226 | 18.3 s |
| Ross 128 | 340 | 299 | 19.5 s |
| Epsilon Indi B | 458 | 435 | 22.4 s |
| WISE 0855-0714 | 258 | 6 (5 stacks) | 23.0 s |
| RW Aurigae | 335 | 272 | 29.5 s |
| Barnard's Star | 358 | 341 | 35.3 s |
| Kapteyn's Star | 383 | 353 | 36.1 s |
| R Draconis | 539 | 517 | 45.4 s |

Run time grows with the number of exposures. Saturated stars also need the
wider wing fit (a 25 x 25 pixel window instead of 7 x 7).

## Photometric and astrometric accuracy

Accuracy is part of performance for this product. The figures are derived in
[measurement pipeline](measurement-pipeline.md).

| Quantity | Value | Basis |
| --- | --- | --- |
| Aperture flux of unsaturated stars against 2MASS | within 2-4 % | 81 calibration stars |
| Halo flux of saturated stars against 2MASS, 0.15-20 Jy | median ratio 0.90-1.06, scatter 11-25 % | same |
| Single-exposure position | 0.2-0.5 arcsec | Residuals of the track fits (RMS 0.26-0.95 arcsec including saturated stars) |
| Agreement of the Level 2 WCS with Gaia | about 0.05 arcsec | Pointing check on reference stars |
| Proper motion of fast movers | recovered within the stated errors of 50-1000 mas/yr | Five challenge targets and four further nearby stars |
| Relative brightness between sky passes | 2 % (PSF fluxes), 12 % (ring fluxes) | Non-variable stars |
| Coarse-lattice pixel mapping | error below 1/1000 pixel | Unit test |

## What makes it fast

The first working version of the 7 x 7 field took 166 s on a 4-core machine. The
current version takes 51 s on the same machine and 37-52 s in production. The
changes, in order of effect:

| Optimisation | Effect | Where |
| --- | --- | --- |
| HTTP range requests instead of whole files | 35-50 times fewer bytes | `cutout.cut_async` |
| Hosting in the same AWS region as the bucket | Read rates of 85-220 MB/s | `render.yaml` (`region: virginia`) |
| Worker processes, one per CPU, each with its own event loop | CPU work scales with cores; the interpreter lock no longer serialises it | `worker.py`, `service._fetch_in_processes` |
| Exactly one web process | Removed a fourfold duplication of every fetch (see below) | `render.yaml`, `--workers 1` |
| One mosaic per stage instead of one cutout per cell | Each exposure is read once per stage, not up to 49 times | `tiles.py` |
| Parallel point queries on a lattice instead of one area query | About 2 s per point query in parallel against 18-55 s for an area query | `Grid.search_points`, `sia.find_exposures_at` |
| CSV instead of VOTable from the archive | Parsing drops from about 0.2 s to a few milliseconds per answer; under half the bytes | `sia.parse_sia_csv` |
| Exact pixel mapping on a coarse lattice with spline interpolation | Removed a third of the CPU time of wide fields | `cutout.detector_coords` |
| Parsing only the needed header cards | Avoids parsing about 300 cards per file | `cutout._parse_header` |
| Sky plane fitted on a 40 000 pixel subsample | Constant cost regardless of cutout size | `cutout._background_plane` |
| One numeric thread per process | No thread contention inside the container | `sphereblink/__init__.py` |
| Batched writes, WAL mode, `synchronous=NORMAL` | Far fewer fsyncs when storing frames | `service.Store` |
| Half-precision storage of wide frames | Halves the largest cache items | `Store.put_frames` |
| Worker pool warmed at start | The first view after a restart does not wait for imports | `Service.warm_up` |
| Epoch cache | The page asks for the same epochs several times (cube, picture, movie); stacking a wide mosaic takes seconds | `main._epochs` |
| Asinh-compressed 16-bit cube with gzip | 2 bytes per pixel with full dynamic range | `render.encode_cube` |
| Client-side image arithmetic and stretch | Mode, reference and contrast changes cost no request | `web/app.js` |
| Staged field loading | Centre visible after 14-20 s instead of after the whole block | `tiles.stage_levels` |

### A lesson from deployment

The hosting platform sets `WEB_CONCURRENCY` to the CPU count, which uvicorn
reads as the number of server processes. With four of them, each held its own
jobs in memory: every view was fetched four times in parallel and progress polls
were answered by whichever process received them. Early measurements of
"24-60 MB/s from S3" were an artefact of this. With a single web process the
real rate is 85-220 MB/s. The start command now pins one worker, and
`/api/health` exposes an instance identifier to detect a regression.

## Capacity and limits

| Limit | Value | Setting |
| --- | --- | --- |
| Largest field of view | 51 arcmin (7 x 7 cells is 50.4) | `SPHEREX_MAX_FOV_ARCMIN` |
| Largest progressive level | 3 | `tiles.MAX_LEVELS` |
| Exposures per view | 1200, thinned evenly in time beyond that | `SPHEREX_MAX_FRAMES` |
| Concurrent exposure reads | 32, shared by the worker processes | `SPHEREX_FETCH_WORKERS` |
| Worker processes | 4 | `SPHEREX_FETCH_PROCS` |
| Pixels per cube response | 40 million | `main.CUBE_BUDGET` |
| Frames held in memory | 2.5 GB in production (default 600 MB) | `SPHEREX_RAM_BUDGET_MB` |
| Epoch cache | 500 MB | `main.EPOCH_CACHE_BYTES` |
| Disk cache | 16 GB of the 20 GB disk in production (default 4 GB) | `SPHEREX_DISK_BUDGET_GB` |
| Concurrent measurements | 1 process; further jobs queue | `measure_api._executor` |
| Measurement time limit | 600 s | `measure_api.JOB_TIMEOUT_S` |

Planning numbers:

- **Disk.** An 8 arcmin view occupies about 41 MB, so the production budget
  holds roughly 400 such views before the least recently fetched are dropped.
  A dropped view is fetched again on demand.
- **Concurrency.** Views in progress share the four worker processes. Two cold
  7 x 7 fields started together finished in 54 s and 77 s instead of about 45 s
  each. Warm views are served from memory or disk and do not use the workers.
- **Shared work.** Any number of users opening the same view share one job.
- **Scaling path.** The design scales vertically (more CPUs means more worker
  processes; set `SPHEREX_FETCH_PROCS` to match). Horizontal scaling would
  require moving job state out of process memory, which was not needed at
  the present load.

## Bottlenecks that remain

| Bottleneck | Share | Possible improvement |
| --- | --- | --- |
| Archive search (IRSA SIA) | About 8 s of a cold seed cell; about 2 s per point query | Cache exposure footprints locally and search them geometrically |
| Reading and resampling a 7 x 7 field | Most of the 37-52 s after the search: 5.9-7.2 GB and several hundred 493-pixel frames on 4 cores | More cores; batch resampling |
| Single measurement process | Jobs queue behind one another | A second process when demand requires it |
| Cube size for long exposure-by-exposure series | Tens of megabytes for wide fields | Streaming or per-epoch requests |
