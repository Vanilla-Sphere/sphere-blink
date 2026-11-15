# Deployment and operations

This document describes the production service. The dev site in front of it,
and the path a change takes from a branch to production, are in
[environments](environments.md).

## Production environment

| Item | Value |
| --- | --- |
| Public address | <https://vanillasphere.com> (primary), <https://www.vanillasphere.com> (redirects to the primary) |
| Hosting | Render web service, defined by the blueprint `render.yaml` |
| Region | Virginia (US East), the same AWS region as the SPHEREx bucket |
| Plan | Pro Plus: 4 CPU, 8 GB memory |
| Runtime | Python 3.12.7 |
| Persistent disk | 40 GB mounted at `/var/data` |
| Measurement database | Supabase Postgres in `us-east-1`, reached through the transaction pooler |
| TLS | Certificates issued and renewed by the hosting platform |
| DNS | Apex `A` record and `www` `CNAME` to the hosting platform, at the domain registrar |

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/diagrams/deployment-dark.png">
  <img alt="Deployment diagram showing the web service and its disk on Render in Virginia, next to the Postgres database and the SPHEREx bucket in AWS us-east-1." src="img/diagrams/deployment.png">
</picture>

*Figure: Production environment. Source: [`diagrams/deployment.html`](diagrams/deployment.html).*

### Why this placement

The service reads hundreds of megabytes to several gigabytes from S3 for every
cold view. Placing it in the same AWS region as the bucket gives read rates of
85-220 MB/s and no cross-region latency on the four requests made per exposure.
The measurement database is in the same region for the same reason.

## Blueprint

`render.yaml` is the source of truth for the service:

```yaml
services:
  - type: web
    name: sphere-blink
    runtime: python
    region: virginia
    plan: pro plus
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn sphereblink.main:app --host 0.0.0.0 --port $PORT --workers 1
    healthCheckPath: /api/health
    domains: [vanillasphere.com, www.vanillasphere.com]
    disk: {name: cache, mountPath: /var/data, sizeGB: 20}
```

(Abbreviated; see the file for the environment variables and comments.)

Three points are essential:

1. **`--workers 1` and `WEB_CONCURRENCY=1`.** The application keeps jobs in the
   memory of the web process and starts its own worker processes for the heavy
   work. The platform otherwise sets `WEB_CONCURRENCY` to the CPU count, which
   would start four independent servers. See
   [architecture](architecture.md#concurrency-model).
2. **The cache directory is on the persistent disk.** `SPHEREX_CACHE_DIR` points
   to `/var/data/sphereblink`, so cached frames and stored measurements survive
   deploys and restarts.
3. **Process count, memory budget and disk budget follow the plan.** Keep
   `SPHEREX_FETCH_PROCS` equal to the CPUs of the plan, `SPHEREX_RAM_BUDGET_MB`
   well below its memory, and `SPHEREX_DISK_BUDGET_GB` plus
   `SPHEREX_ATLAS_BUDGET_GB` (the sky photograph's tiles share the disk) at about
   85 % of the disk at most (28 of the 40 GB in production).

## Configuration

All settings are environment variables, read in `sphereblink/config.py` unless
noted.

| Variable | Default | Production | Meaning |
| --- | --- | --- | --- |
| `SPHEREX_SIA_URL` | `https://irsa.ipac.caltech.edu/SIA` | default | Exposure search service |
| `SPHEREX_COLLECTIONS` | `spherex_dr1,spherex_qr3` | default | Collections to search, newest first |
| `SPHEREX_S3_HTTP_BASE` | `https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com` | default | Where files are read from |
| `SPHEREX_CACHE_DIR` | `.cache/sphereblink` | `/var/data/sphereblink` | Location of the SQLite files |
| `DATABASE_URL` | empty | set in the dashboard | Postgres connection string; empty means local storage only |
| `SPHEREX_MAX_FOV_ARCMIN` | `51` | default | Largest field of view |
| `SPHEREX_WIDE_FOV_ARCMIN` | `12` | default | Above this a view is a mosaic and searches on a lattice |
| `SPHEREX_MAX_FRAMES` | `1200` | default | Exposures per view before thinning |
| `SPHEREX_FETCH_WORKERS` | `64` | default | Concurrent exposure reads, shared by the worker processes |
| `SPHEREX_MIRROR_HTTP_BASE` | `https://irsa.ipac.caltech.edu/ibe/data/spherex` | default | Second source of the same files, used when the bucket fails |
| `SPHEREX_KEEP_FEATURED` | `1` | default | After start, fetch the 8 arcmin views of the featured targets if they are not on disk; they are never evicted |
| `SPHEREX_REQUEST_THREADS` | `160` | default | Threads that answer requests |
| `SPHEREX_NAME_TTL_DAYS` | `30` | default | How long a resolved object name is kept on disk |
| `SPHEREX_RATE_*`, `SPHEREX_MAX_*` | see [security](security.md#request-limits) | default | Request limits per caller. The value without a row in the table `settings`: see [settings in the database](#settings-in-the-database) |
| `SPHEREX_SETTINGS_REFRESH_S` | `30` | default | How often the table `settings` of the database is read, in seconds (5 at least) |
| `SPHEREX_ACTIVE_VIEWS` | `SPHEREX_FETCH_PROCS` | `4` | Views whose exposures are cut at the same time; the next ones wait in a queue. `0` removes the limit. Sized to the machine: see [performance](performance.md#how-many-jobs-at-once). This one, the three below and `SPHEREX_RAM_BUDGET_MB` are the values without a row in the table `settings` |
| `SPHEREX_ACTIVE_MB` | `1200` | `2400` | Frames (MB) the views being cut may hold in memory together; a wide field that does not fit waits for another to finish. `0` removes the limit |
| `SPHEREX_QUEUE_PREVIEW` | `1` | default | A view in the queue gets its first four exposures at once; `0`: nothing before its turn |
| `SPHEREX_MEASURE_PROCS` | `2` | `2` | Measurements running at the same time, each in a process of its own; the next ones wait in a queue |
| `SPHEREX_MOVER_SEARCH` | `1` | default | `0` turns the blind search for moving objects off; the log stays readable |
| `SPHEREX_MOVER_STACK` | `1` | default | `0` turns the second search of a field (faint and slow objects, one to three minutes more) off |
| `SPHEREX_MOVER_STACK_BUDGET_S` | `180` | default | Seconds after which that search skips its four-day windows |
| `SPHEREX_MOVER_MIN_FREE_MB` | `1800` | default | Memory that must be free in the container before a field is searched |
| `SPHEREX_FETCH_PROCS` | `min(4, CPU count)` | `4` | Imaging worker processes |
| `SPHEREX_RAM_BUDGET_MB` | `600` | `2000` | Frames kept in memory |
| `SPHEREX_TRIM_S` | `15` | default | Every process gives the memory it has freed back to the system at this interval while it works (seconds); `0` switches it off. See [performance](performance.md#memory-after-a-busy-hour) |
| `SPHEREX_DISK_BUDGET_GB` | `4` | `16` | Size of the frame cache on disk |
| `SPHEREX_ATLAS_BUDGET_GB` | `1` | `12` | Tiles of the sky photograph kept on disk, in all; above it the deep tiles least recently asked for are deleted |
| `SPHEREX_ATLAS_KEEP_ORDER` | `3` | `6` | Tiles of this HiPS order and below are never deleted (orders 0-5 are 1.65 GB, 0-6 about 7.2 GB; one more is 29 GB more) |
| `SPHEREX_ATLAS_SEED` | `0` | `1` | After start, fetch every tile of those orders that is not on disk yet (about 40 minutes the first time, nothing afterwards; it stops with less than 4 GB free on the disk) |
| `SPHEREX_ATLAS_URL` | IRSA's 2MASS colour HiPS | default | Where the tiles are fetched from |
| `SPHEREX_ATLAS_FETCHES` | `8` | default | Tiles fetched from the archive at the same time |
| `SPHEREX_AUTO_MEASURE` | `1` | default | Measure the featured targets after start; `0` disables (read in `measure_api.py`) |
| `SPHEREX_AUTO_MEASURE_DELAY` | `40` | default | Seconds after start before automatic measurement begins |
| `SPHEREX_WARM_CATALOGS` | `1` | default | After start, store the Gaia and CatWISE overlays of the featured targets for every field size the viewer offers; `0` disables (read in `config.py`) |
| `WEB_CONCURRENCY` | - | `1` | Must be 1 |
| `PYTHON_VERSION` | - | `3.12.7` | Runtime version for the platform |

### Settings in the database

The limits somebody may want to turn during a busy hour are rows of the table
`settings` in the team database: how many views and measurements the pipeline
works on at a time, the memory their frames may take, how much may wait, and
how much one caller may ask for. The server reads the table every 30 seconds
(`sphereblink/settings.py`), so a changed value is in force within the minute,
**without a deploy and without a restart** (a deploy takes the site down for
about half a minute and forgets the views being gathered).

To change a value, either

- open the table `settings` in the Supabase table editor, change the cell
  `value` of the row and save; or
- run one statement in the SQL editor:

  ```sql
  update settings set value = 6 where key = 'active_views';
  ```

Then open [`/api/settings`](api-reference.md#get-apisettings): it lists every
setting with the value in force, where it comes from and when the table was
read last. The server's log has a line for every change
(`settings: active_views is now 6 (was 4)`).

| Row | Production | Range | What it limits |
| --- | --- | --- | --- |
| `active_views` | 4 | 1 to 8 | Views whose exposures are cut at the same time; the next ones wait in the queue |
| `active_mb` | 2400 | 300 to 4000 | Memory (MB) the views being cut may fill with frames together |
| `measure_procs` | 2 | 1 to 4 | Measurements running at the same time |
| `queue_preview` | 1 | 0 or 1 | A view in the queue gets its first four exposures at once |
| `ram_budget_mb` | 2000 | 300 to 4000 | Memory (MB) for the frames of views already gathered |
| `max_jobs`, `max_jobs_per_client` | 96, 24 | 8 to 400, 4 to 96 | Views gathered or waiting at once: in all, and for one caller |
| `max_fields_per_client` | 8 | 1 to 32 | Wide fields loading at once for one caller |
| `max_measure_queue` | 12 | 2 to 48 | Measurements waiting or running |
| `max_open_requests` | 32 | 8 to 128 | API calls being answered at once for one caller |
| `rate_requests_per_s`, `rate_requests_burst` | 40, 1200 | 5 to 400, 100 to 12000 | API calls of one caller: sustained, and at once |
| `rate_names_per_min` | 40 | 5 to 600 | Names one caller may send to the name services |
| `rate_catalogs_per_min` | 60 | 5 to 600 | Catalogue and archive queries that go out for one caller |
| `rate_tiles_per_s`, `rate_tiles_burst` | 100, 3000 | 10 to 1000, 200 to 30000 | Tiles of the sky photograph for one caller |
| `rate_atlas_per_min` | 1200 | 60 to 6000 | Sky tiles the server fetches from the archive for one caller |
| `rate_measures_per_hour`, `rate_measures_burst` | 30, 6 | 1 to 600, 1 to 60 | New measurements of one caller |

What the table cannot do is take the server down:

- **A value is only taken inside its range.** The ranges are written in the
  code (`settings.SETTINGS`), next to the meaning of each row. The table
  carries the same numbers in `min_value` and `max_value` and refuses a value
  outside them at once, so whoever types it sees it; if that check is ever
  removed or the range columns are edited, the server still moves the value
  to the nearest end of its own range. A value that is not a number is
  ignored. No row can remove a limit (0 is outside every range).
- **The memory is held together.** `ram_budget_mb` + `active_mb` + 250 MB for
  each of `measure_procs` never come to more than the same sum of the
  environment variables, which is what the machine was sized for (4900 MB in
  production, the worst case of 7.0 of 8 GB in
  [performance](performance.md#how-many-jobs-at-once)). The table can move
  memory between the three, not add any: when the sum is above it,
  `ram_budget_mb` is lowered first (a view forgotten is only read from disk
  again), then `active_mb`, and `/api/settings` says so in `note`.
- **A row that is missing has the value of the environment variable** of the
  same name (`SPHEREX_ACTIVE_VIEWS` for `active_views`, and so on), or its
  default in `config.py`. Deleting a row is how a setting goes back to it.
- **The database is not needed.** The table is read in a thread of its own
  and no request ever waits for it. When the database does not answer, the
  values read last stay in force; after a start without the database, those
  of the environment. `/api/settings` then shows `problem` and how long ago
  the table was read.
- **The web service only reads the table** (role `blink_service`: `select`).
  It is changed by the owner of the database.

A change to a limit takes effect on what comes next. Raising `active_views`
gives the first views of the queue their turn at once; lowering it takes
nobody's place away, and the next ones wait until enough have finished.
Measuring processes started for a higher `measure_procs` are let go the next
time nothing is being measured.

Settings that are read once at a start stay environment variables: the
number of worker processes, the request threads, the disk budgets, the
addresses of the archives. A new machine size is still a change to
`render.yaml` ([changing capacity](#changing-capacity)); raise the ranges in
`settings.SETTINGS` and the rows of `sql/settings.sql` with it.

### Secrets

`DATABASE_URL` is the only secret. It is entered in the hosting dashboard
(`sync: false` in the blueprint) and never appears in the repository. It uses the
role `blink_service` through the provider's transaction pooler; the driver is
configured without prepared statements (`prepare_threshold=None`), as that
pooling mode requires. To rotate the password, change the role's password in
the database and update the variable in the dashboard together.

## Database setup

On a fresh Postgres 15+ database, once:

```bash
psql "$OWNER_URL" -f sql/schema.sql    # tables, indexes, sources_near, row level security
psql "$OWNER_URL" -f sql/settings.sql  # the limits changed while the server runs, with the values of production
psql "$OWNER_URL" -f sql/roles.sql     # roles blink_service, blink_loader, blink_web and policies
```

Then set a password for each role that will be used (`alter role ... password
'...'`) and share it privately. The scripts are idempotent for the tables and
columns (`create table if not exists`, `add column if not exists`).

The service does not need the database to run. Without `DATABASE_URL`,
the limits are those of the environment, measurements are kept in the local
SQLite store and `/api/tracks` serves them from there. When a database is connected later, results stored locally are
written to it at the next start.

## Release process

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/diagrams/release-flow-dark.png">
  <img alt="Flow diagram showing a pull request merged to main, then build, start, health check and going live, followed 40 seconds later by the database sync and re-measurement." src="img/diagrams/release-flow.png">
</picture>

*Figure: Release process. Source: [`diagrams/release-flow.html`](diagrams/release-flow.html).*

1. Changes are made on a branch (`feat/...`, `fix/...` or `chore/...`), merged to
   `develop` through a pull request, looked at on the dev site, and promoted
   by a pull request from `develop` to `main`. See
   [environments](environments.md).
2. A push to `main` triggers a deploy. Build and start take about 70 s. This
   applies to every merge, including one that changes only `docs/`, unless the
   deploy is skipped (see below).
3. Because the service has an attached disk, the old instance stops before the
   new one starts: there is an interruption on each deploy. On 2026-11-14 the
   site answered HTTP 502 for at least 35 s during a deploy; the logs showed
   that 30 s of it was the old server waiting for open connections until the
   host killed it. The server now leaves at once
   (`--timeout-graceful-shutdown 3` and `main._stop_fast`): at the deploy of
   08:23 UTC the same day it was gone 0.1 s after the stop signal, and the
   site was away for 27 s. That remainder is the host's: 17 s until the new
   instance's process starts and 10 s until its health check passes. It
   cannot be shortened from the code while one instance holds the disk. The
   viewer waits and retries by itself while the site is away.
4. Jobs in memory are lost on a deploy. Cached frames remain on disk, so open
   views resume from the cache when requested again. A measurement in progress
   is abandoned and can be requested again.
5. Forty seconds after start, the service pushes any locally stored results the
   database lacks and re-measures featured targets whose results are missing,
   older than seven days, or from an earlier pipeline version.

### Merging without a deploy

A change that does not affect the running service, such as an edit to `docs/`,
does not need a deploy. Render skips the automatic deploy when the commit
message contains `[skip render]` (its documentation also lists `[render skip]`,
`[skip deploy]` and `[skip cd]`). Put the phrase in the commit message and in
the title of the merge commit. The skipped deploy is listed on the service's
Events page, and the next ordinary merge deploys as usual.

`render.yaml` also lists `docs/`, `tests/`, `scripts/`, `.github/`, `render.dev.yaml`
and Markdown files under `buildFilter.ignoredPaths`, so a merge that touches nothing else is skipped even
without the phrase.

Do not use the phrase on a change to code, `web/`, `requirements.txt` or
`render.yaml`: the service would keep running the previous build.

### Post-deploy checks

After every merge to `main` two checks appear on the merge commit
([environments](environments.md#ui-tests-after-every-merge)): `ui-tests /
main`, the UI tests against the merged commit started inside the job, and
`ui-tests / production serves the commit`, which waits until production
serves that commit. The checks below look at the engine.

| Check | Expected |
| --- | --- |
| `GET /api/health` twice | `ok: true` and the same `instance` value both times |
| `GET /api/view?target=barnard&fov=8` | `job.status` reaches `ready`; `failed` is 0 |
| `GET /api/frame.png?target=barnard&fov=8` | A PNG image |
| `GET /api/measured` | `database: true`; the five featured targets listed after a few minutes |
| Open `/` | The sky map draws; a click opens a view |

### Warming the cache

After a deploy to a new disk, or when a new data release appears:

```bash
python scripts/prewarm.py https://vanillasphere.com --fov 4,8,15 [coords.txt]
```

This opens every featured target (and every `ra dec` line of the optional file)
at each field of view, waits for the server to read all exposures, and prints
the time taken.

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn sphereblink.main:app --port 8000
# open http://localhost:8000/sky?target=barnard
```

Requirements: Python 3.12 (the production version; the tests also pass on 3.13)
and outbound HTTPS to the archive services.
No account, key or database is needed. The cache is created under
`.cache/sphereblink`. Set `SPHEREX_AUTO_MEASURE=0` to prevent the automatic
measurement of the featured targets during development, and
`SPHEREX_FETCH_PROCS` to the number of cores available.

Tests:

```bash
pip install pytest
SPHEREX_AUTO_MEASURE=0 pytest -q
```

## Monitoring

| Signal | Source | Meaning |
| --- | --- | --- |
| Liveness | `/api/health`, polled by the platform | The web process answers |
| Single-process invariant | `instance` in `/api/health` | Must not change between calls except across a restart |
| Job statistics | `job.stats` in `/api/view`, `/api/cube`, `/api/tiles` | Seconds by phase, megabytes read, request count, failed exposures |
| Measurement state | `/api/measured` | Pipeline version, database connection, running jobs, stored results |
| Application log | Platform log stream | Warnings for failed name lookups, database write failures, failed measurements |
| Memory really held | `memory` in `/api/health` | `used_mb` against `limit_mb`: what cannot be given back. The platform's graph is `working_set_mb`, which also counts the file cache in recent use, so read this before concluding that memory is short; `oom_kills` above 0 means it was. `processes` says which process holds it, `handed_back` that freed memory is being returned |
| Resource use | Platform metrics | CPU, memory (the file cache in recent use included), disk |

## Runbook

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Progress jumps backward or differs between polls; views load several times slower than expected | More than one web process | Confirm with `/api/health` (`instance` changes); restore `--workers 1` and `WEB_CONCURRENCY=1` |
| New views fail with 502 "NASA's SPHEREx archive (IRSA) is not answering right now" | IRSA SIA gave no answer for a minute of asking, and the place has no stored exposure list | Check the archive's status; previously opened views keep working from the cache. The log lines "answered at try N" and "gave no answer in N tries" (`retry.py`) say how often the archive stumbles |
| Many exposures counted as `failed` | S3 or network errors | The job completes with the rest. Failed exposures are not cached, so they are read again the next time the view is loaded from disk (after it has left memory, or after a restart) |
| Name search answers 503 | CDS name services unreachable | Coordinates and featured targets still work; add a mirror in `resolve.SESAME_URLS` if an outage persists |
| Overlays do not load | IRSA TAP slow or down | The viewer retries three times; untick and tick the overlay to retry |
| `/api/measured` shows `database: false` or results with `in_database: false` | `DATABASE_URL` missing, wrong, or the role cannot write | Fix the variable; results are written at the next start |
| Memory pressure | Frame budget too high for the plan | Lower `ram_budget_mb` or `active_mb` in the table `settings` (in force within a minute); views are reloaded from disk when needed |
| Visitors wait in the queue while the machine has room, or a classroom behind one address is refused with 429 | A limit too low for the hour | Raise the row in the table `settings` ([settings in the database](#settings-in-the-database)); no deploy |
| A changed row of `settings` has no effect | Outside its range, the memory rule, or the database does not answer | `/api/settings`: `note` of the row, `problem`, `read_seconds_ago` |
| Disk full | The two disk budgets together above the disk size, or other files on the disk | Lower `SPHEREX_DISK_BUDGET_GB` or `SPHEREX_ATLAS_BUDGET_GB`; each cache trims itself after its next fetch. `/api/atlas` reports the free space; below 1 GB no new sky tile is stored |
| Stored measurements look outdated after a method change | Pipeline version not raised | Raise `measure_api.VERSION` and deploy |
| A view shows stale pixels after a change to the cutout code | Frame cache key not versioned | Raise the version tag in `cutout.cache_key` and deploy |

## Changing capacity

| Change | Steps |
| --- | --- |
| Larger plan | Change `plan` in `render.yaml`; set `SPHEREX_FETCH_PROCS` to the new CPU count and raise `SPHEREX_RAM_BUDGET_MB` and `SPHEREX_ACTIVE_MB` accordingly (their sum is the memory the table `settings` may hand out), then the rows of the table |
| More or fewer jobs at a time on the same machine | Change the row in the table `settings`: [settings in the database](#settings-in-the-database) |
| Larger disk | Change `sizeGB`; raise `SPHEREX_DISK_BUDGET_GB` and `SPHEREX_ATLAS_BUDGET_GB` to about 85 % of it together |
| New data release | Add the collection name at the front of `SPHEREX_COLLECTIONS`; exposure lists refresh within 24 hours |

## Cost drivers

The running cost is the hosting plan, the disk and the database tier. The
archive data are free to read: the bucket is public and needs no account. No
per-request third-party service is used.
