# Performance

All figures in this document were measured on 14 November 2026. Each table
states where. "Production" is the deployed service: Render web service, Pro Plus
plan (4 CPU, 8 GB), region Virginia, 40 GB persistent disk (20 GB until 2026-11-14). "Cold" means nothing
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
| Measurement of one object | 18-45 s typical, 63 s observed | 258-539 stamps of about 0.4 MB |

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
| Proper motion of fast movers | recovered within the stated errors of 50-1000 mas/yr | Five featured targets and four further nearby stars |
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
| Concurrent exposure reads | 64, shared by the worker processes | `SPHEREX_FETCH_WORKERS` |
| Views gathered or waiting | 24 per caller, 96 in total | `SPHEREX_MAX_JOBS_PER_CLIENT`, `SPHEREX_MAX_JOBS` |
| Views worked on at once | 4 in production (one per worker process), holding at most 2400 MB of frames together; further views queue | `SPHEREX_ACTIVE_VIEWS`, `SPHEREX_ACTIVE_MB` |
| Request threads | 160 | `SPHEREX_REQUEST_THREADS` |
| Worker processes | 4 | `SPHEREX_FETCH_PROCS` |
| Pixels per cube response | 40 million | `main.CUBE_BUDGET` |
| Frames held in memory | 2 GB in production (default 600 MB) | `SPHEREX_RAM_BUDGET_MB` |
| Epoch cache | 500 MB | `main.EPOCH_CACHE_BYTES` |
| Disk cache | 16 GB of the 40 GB disk in production (default 4 GB) | `SPHEREX_DISK_BUDGET_GB` |
| Sky photograph tiles on disk | 12 GB in production (default 1 GB), of which orders 0-6 (about 7.2 GB) are never dropped | `SPHEREX_ATLAS_BUDGET_GB`, `SPHEREX_ATLAS_KEEP_ORDER` |
| Concurrent measurements | 2 processes; further jobs queue, 12 waiting or running at most | `SPHEREX_MEASURE_PROCS`, `SPHEREX_MAX_MEASURE_QUEUE` |
| Measurement time limit | 600 s from the moment the job starts measuring | `measure_api.JOB_TIMEOUT_S` |

Planning numbers:

- **Disk.** An 8 arcmin view occupies about 41 MB, so the production budget
  holds roughly 400 such views before the least recently fetched are dropped.
  A dropped view is fetched again on demand.
- **Sky photograph.** A tile of the 2MASS atlas is about 100 KB (measured on
  the archive, 2026-11-14, 40 tiles per order): orders 0-3 are 0.08 GB, 0-4
  0.36 GB, 0-5 1.65 GB, 0-6 7.2 GB, all ten orders about 445 GB. Production
  keeps orders 0-6 (65 532 tiles: the whole sky at 6.4 arcsec a pixel, the
  size of a SPHEREx pixel) and about 35 000 deeper tiles, each a tenth to
  half of a degree of sky. A tile on disk is answered in
  about 2 ms; one that has to be fetched from the archive took 0.7 s in a
  test. Filling orders 0-2 (252 tiles) took 9 s with four connections; in
  production the server fetched about 25 tiles a second.
- **Sky photograph on the wire.** The viewer downloads a tile as WebP
  (quality 80), made from the JPEG on first use and kept beside it. Measured
  on 2026-11-15 on 70 tiles of orders 2 to 8 from ten parts of the sky
  (the galactic centre, Orion, the Large Magellanic Cloud, the north
  galactic pole and others): 34 % of the JPEG's bytes together, from 10 %
  for a tile of sparse sky to 53 % for one of the galactic centre, 24 % for
  the tile in the middle; making one takes 30 ms
  of one processor, and two at most are made at a time. After the page's
  cleaning the two pictures differ by a peak signal-to-noise ratio of 46 dB
  in the middle and 31 dB in the densest star field; side by side on a
  screen no difference showed. In a browser, nothing cached: the start
  screen's 22 tiles are 0.63 MB instead of 2.22 MB (the whole first visit
  0.99 MB instead of 2.58 MB); that screen and two zooms into the galactic
  centre, 62 tiles, 6.3 MB instead of 13.3 MB (63 tiles). On disk the copies add about a third
  to the tiles that have been looked at; they are counted in
  `SPHEREX_ATLAS_BUDGET_GB` (in production orders 0-6 are 7.7 GB of the 12;
  with a copy of every one of them, at the 34 % of the test tiles, 10.4 GB).
- **Measured for a visitor.** In a browser in Vietnam on 2026-11-14, 24 tiles
  of order 2 asked six at a time, nothing cached: 1.4 to 1.8 s from our
  server, 3.3 to 4.2 s from the archive itself.
- **Concurrency.** Views in progress share the four worker processes. Two cold
  7 x 7 fields started together finished in 54 s and 77 s instead of about 45 s
  each. Warm views are served from memory or disk and do not use the workers.
- **Shared work.** Any number of users opening the same view share one job.
- **Scaling path.** The design scales vertically (more CPUs means more worker
  processes; set `SPHEREX_FETCH_PROCS` to match). Horizontal scaling would
  require moving job state out of process memory, which was not needed at
  the present load.

## Several visitors at once

Measured on a 4-core machine on 2026-11-14 (not in production), before the
pipeline queue of the next section: eight visitors open eight different cold
8 arcmin views (249-341 exposures each) at the same moment.

| | First frames on screen (slowest visitor) | All frames, mean | All frames, slowest |
| --- | --- | --- | --- |
| Chunks handed to the pool view by view (before) | 34.8 s | 23.6 s | 40.8 s |
| Fair scheduler, 64 concurrent reads (now) | 3.6 s | 18.2 s | 31.4 s |

A single cold view on the same machine went from 9.1-9.6 s to 8.1 s (the reads
wait on the network for more than half of their time, so 64 concurrent reads
keep the four processes busier than 32 did). The total work is unchanged: the
scheduler decides who waits, and nobody waits without a picture.

Other changes of the same date: the disk cache evicts by last use and keeps a
running size per view (it used to scan the whole frame index after every cold
view, before the view was reported ready); cubes are compressed outside the
event loop; the cube of a finished view may be kept by the browser for ten
minutes; resolved names survive a restart.

## How many jobs at once

The pipeline works on a limited number of jobs and queues the rest (see
[architecture](architecture.md#concurrency-model)). The limits are settings
(rows of the table `settings` since 2026-11-15, changed without a deploy:
[deployment](deployment.md#settings-in-the-database)),
and their production values were chosen from the measurements below, made on
2026-11-14 on a 4-core machine with the production settings (4 worker
processes, 64 concurrent reads, 2000 MB of frames kept in memory). The
production machine has 4 CPUs and 8 GB; memory there had reached 6.1 GB earlier
the same day, before any of these limits existed.

**Ten visitors, ten different cold 8 arcmin views (2869 exposures) at the same moment:**

| Views worked on at once | First picture (slowest visitor) | All frames, mean | All frames, slowest | Peak memory | CPUs in use |
| --- | --- | --- | --- | --- | --- |
| 1 | 4.6 s | 26.7 s | 45.5 s | 1.06 GB | 2.7 |
| 2 | 6.1 s | 27.4 s | 42.9 s | 1.07 GB | 2.8 |
| 3 | 6.6 s | 26.5 s | 44.0 s | 1.09 GB | 2.9 |
| 4, frames up to 2400 MB (chosen) | 5.1 s | 26.8 s | 48.0 s | 1.08 GB | 2.6 |
| No limit | 5.6 s | 25.5 s | 42.6 s | 1.08 GB | 3.0 |
| 2, waiting views get nothing before their turn | 35.4 s | 25.4 s | 43.4 s | 1.05 GB | 2.9 |

For small views the number hardly matters. The machine delivers about 67
exposures per second whatever the limit, because the four worker processes
and their 64 reads from the bucket are the bottleneck, and memory does not
move. What matters is the first chunk of four exposures that a waiting view
gets at once: without it the last visitor looks at an empty screen for 35 s.

The same test on the production service (2026-11-14, after the deploy of
these limits; ten cold 8 arcmin views, 2910 exposures, all asked for from one
address): every visitor had a first picture within 2.8-3.6 s, all frames
after 27.3 s on average and 45.7 s for the last one, with queue positions up
to 7. Nothing failed and nothing was refused. A further visitor who kept
asking for the health check, the featured targets and a stored view got 157
answers out of 157, with a median of 120 ms (the same as on the idle server)
and 228 ms at most. The service used 1.07 GB of its 8 GB.

**The heavy mix: four 7 x 7 fields, six 8 arcmin views and four measurements at the same moment:**

| Views worked on at once | Peak memory (web process + child processes) | Slowest small view: first picture, all frames | Slowest field | Slowest measurement |
| --- | --- | --- | --- | --- |
| 2 | 4.89 GB (2.95 + 2.15) | 10.7 s, 63.9 s | 190.5 s | 73.4 s |
| 4, frames up to 2400 MB (chosen) | 4.88 GB (2.71 + 2.22) | 9.1 s, 46.0 s | 177.5 s | 69.6 s |
| No limit | 5.02 GB (2.90 + 2.24) | 9.2 s, 39.1 s | 185.5 s | 69.6 s |

Everything finished in every run, at about 60% of the production machine's
memory. A limit of two makes small views wait behind two wide fields (64 s
instead of 39-46 s) and buys nothing in memory for this mix.

**Six measurements asked for at the same moment:**

| Measuring processes | All six done | Mean wait of a visitor | Mean run time of one job | Peak memory |
| --- | --- | --- | --- | --- |
| 1 (before) | 101 s | 62.9 s | 15.8 s | 0.78 GB |
| 2 (chosen) | 74 s | 49.3 s | 21.8 s | 1.03 GB |
| 3 | 78 s | 52.2 s | 30.4 s | 1.25 GB |
| 4 | 77 s | 53.6 s | 36.4 s | 1.52 GB |

Two processes finish the queue 27% sooner than one. Three or four finish it
no sooner, because the measurements share 16 connections to the bucket and
each one only runs slower, at about 0.25 GB per process.

**The values chosen for 4 CPUs and 8 GB:**

- `SPHEREX_MEASURE_PROCS=2`: the most that still adds throughput.
- `SPHEREX_ACTIVE_VIEWS=4`: one view per worker process. More would not add
  throughput either; fewer makes small views wait behind wide fields.
- `SPHEREX_ACTIVE_MB=2400`: the memory the views being gathered may fill with
  frames. An 8 arcmin view holds about 9 MB, a 7 x 7 field 0.5-0.7 GB and at
  most 1.17 GB (1200 exposures of 493 x 493 pixels), so this is two fields of
  the largest size, or three typical ones. The worst case is then 0.4 GB
  (idle) + 2.0 GB (frames of finished views) + 2.4 GB (views being gathered)
  + 2.2 GB (worker and measuring processes, as measured) = 7.0 GB of the
  8 GB. A third field of the largest size would take the machine to its
  limit, and a server killed for memory interrupts every visitor, which a
  queue does not.

The queue itself is bounded (24 views per caller, 96 in all, 12
measurements): beyond that a request is refused with `Retry-After`.

The three numbers that fill the memory are held to this sum when they are
changed in the table `settings`: frames kept + frames being gathered + 250 MB
for each measuring process stay at 2000 + 2400 + 2 x 250 = 4900 MB, so the
worst case stays 7.0 GB whatever the table holds. Raising one lowers the
frames kept. Measure again before raising the sum itself (the environment
variables of `render.yaml`).

## Memory after a busy hour

On 2026-11-15 the service stood at 7.7 of 8 GB after an hour with many
visitors and stayed there when it was idle again. `memory` in `/api/health`
showed that this was memory really held, not file cache. The frames kept for
finished views explain 2 GB of it (`SPHEREX_RAM_BUDGET_MB`); most of the rest
was memory the processes had already freed. The C library keeps freed memory
inside the heap of the process for its next request and returns it to the
system only from the top of the heap, so a worker process that had cut a
7 x 7 field went on holding several hundred megabytes for as long as it lived.

Each process of the service (the web process, the four that cut exposures,
the measuring processes and the mover search) now gives its freed pages back
with `malloc_trim` (`memory.keep_trimmed`): every 15 s in which it has worked,
and once more after its work has ended. An idle process is left alone.
`SPHEREX_TRIM_S` sets the interval; `0` switches it off.

Measured on a 4-core machine on 2026-11-15 (not in production), with the
limits of production: four 7 x 7 fields loaded one after the other, each with
its mover search, then two minutes idle; and six measurements, then idle.

| Held when idle again | Before | Now |
| --- | --- | --- |
| The service, after four 7 x 7 fields | 3989 MB | 3114 MB |
| of which the web process (2000 MB of it are kept frames) | 2327 MB | 2120 MB |
| of which the four processes that cut exposures | 1525 MB | 878 MB |
| of which the mover search | 131 MB | 109 MB |
| The two measuring processes, after six measurements | 326 and 310 MB | 117 and 112 MB |

Nothing else changed: the fields and the movers found in them were the same,
and so was the speed (a seed cell took 20.2 s of processor before and 21.5 s
now, its reads 7.9 s and 8.0 s; the next level 33.6 s and 31.9 s, 11.3 s and
9.6 s: the spread between two runs). One hand-back took about 2 ms.
`handed_back` in `/api/health` says how often the web process did it, how much
it gave back and the longest it took.

What it does not recover: a process that cuts exposures settles at 180-300 MB
after several wide fields, not at the 79 MB it starts with. Pages that still
hold one small live object cannot be given back. Starting those processes
afresh now and then would recover that too, at the price of an interruption
of the views being cut; it was not done.

## A server with 2 GB

To cut the cost of production (2026-11-19) the service was made to fit a
plan with 1 CPU and 2 GB. Before, one 7 x 7 field was enough to have the web
process killed for memory at 2 GB: it held 1.98 GB of its own. Two things
filled it, measured in a local test under a 1 CPU / 2 GiB limit:

- **The frames of the field.** A view keeps its frames in memory while it is
  gathered and after; for a 7 x 7 field that is 0.5-1.2 GB (493 x 493
  pixels, float32, one per exposure). Frames wider than 200 pixels
  (`SPHEREX_SPILL_NPIX`) now live in a file on disk that has no name
  (`service.Spill`): every frame's image is a window on it. What is read sits
  in the kernel's file cache, which the kernel takes back when the memory is
  needed, where memory a process holds for itself cannot be given back
  without a swap the host does not have. The file goes away by itself once
  nothing refers to its frames, so a restart leaves nothing behind. These
  files have their own budget (`SPHEREX_SPILL_BUDGET_GB`); the frames kept
  in memory (`ram_budget_mb`) count only what is really in memory. The
  mover search reads its cube of the field the same way.
- **The exposures being cut.** Each waits for the network holding the band
  of detector rows it reads and the detector position of every pixel of the
  grid: about 2.5 MB for an 8 arcmin view, 16 MB for a 7 x 7 field. 64 of
  them at once were a gigabyte. How many are cut at once now follows their
  size (`SPHEREX_CUT_MB`, the memory they may take together): an 8 arcmin
  view still cuts 64, a 7 x 7 field 24 (with 400 MB). With one CPU there
  are no worker processes and the Scheduler does not run, so `active_views`
  views are cut at a time and each gets its share (with 250 MB and two
  views: 7 exposures of a 7 x 7 field at once, still 64 of an 8 arcmin view).

`/api/cube` also built the cube of a whole field at once: at full
resolution (`bin=1`) 400 MB passed through the web process for a 67 MB
answer. It now encodes one epoch at a time and compresses in pieces; the
bytes sent are the same.

Measured with the values meant for that plan (`SPHEREX_FETCH_PROCS=1`,
`ACTIVE_VIEWS=2`, `ACTIVE_MB=300`, `RAM_BUDGET_MB=250`, `MEASURE_PROCS=1`,
`CUT_MB=250`, `EPOCH_CACHE_MB=120`, `MOVER_MIN_FREE_MB=700`), cold:

| Step | Time | Memory held (not file cache), peak |
| --- | --- | --- |
| 7 x 7 field (363, 427, 521 exposures) | 102 s | 0.43 GB, the web process |
| Its mover search, then the search for faint and slow objects | 245 s | 0.65 GB, web process and search |
| All 38 API checks (while another field was searched for movers), the click-through of the viewer (21 checks) | all pass | 1.08 GB |
| Six cold 8 arcmin views, a cold 7 x 7 field (378, 507, 605 exposures) and a measurement, all at once | 5.5 min until the last; each 8 arcmin view 1.5-3.5 min | 1.07 GB |

No process was killed. The kernel used the rest of the 2 GB as file cache,
which is what the host's graph of memory shows (`memory` in `/api/health`
tells the two apart).

## Bottlenecks that remain

| Bottleneck | Share | Possible improvement |
| --- | --- | --- |
| Archive search (IRSA SIA) | About 8 s of a cold seed cell; about 2 s per point query | Cache exposure footprints locally and search them geometrically |
| Reading and resampling a 7 x 7 field | Most of the 37-52 s after the search: 5.9-7.2 GB and several hundred 493-pixel frames on 4 cores | More cores; batch resampling |
| Reads from the bucket | 64 at a time for views, 16 for measurements; both pipelines wait on them for more than half of their time | A machine closer to the bucket, or more connections if the archive allows them |
| Cube size for long exposure-by-exposure series | Tens of megabytes for wide fields | Streaming or per-epoch requests |
