# API reference

All functionality of the product is available over HTTP. The viewer uses only
the endpoints documented here.

- **Base URL:** `https://vanillasphere.com`
- **Interactive documentation:** `/docs` (OpenAPI schema at `/openapi.json`)
- **Methods:** every endpoint is `GET`
- **Authentication:** none
- **CORS:** any origin, `GET` and `HEAD` only, no credentials; the response headers `X-Job` and `Retry-After` are exposed
- **Limits:** per caller address; a refused request gets `429` or `503` with `Retry-After` (see [Errors](#errors) and [security](security.md))
- **Compression:** responses above 2 kB are gzip-compressed when the client accepts it
- **API version:** 0.2.0

## Conventions

### Addressing a view

Most endpoints act on a *view*: a position and a field of view.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | string | - | Object name, challenge-target key, or coordinates as text (`"269.4486 4.7387"`, `"08h55m10.8s -07d14m42s"`) |
| `ra`, `dec` | float | - | ICRS position in decimal degrees, `0 <= ra < 360` and `-90 <= dec <= 90` (anything else, including `nan` and `inf`, is `400`). When both are given they take precedence; `target` is then used only as the display name (printable text, cut to 80 characters; `/api/measure` ignores it) |
| `fov` | float | `8` | Field of view in arcminutes, greater than 0 and at most 51. Values below 1 are raised to 1 |

Either `target` or both `ra` and `dec` is required. Two requests with the same
resolved position and `fov` address the same view and share its cached data.

### Selecting bands and grouping

| Parameter | Values | Default | Description |
| --- | --- | --- | --- |
| `det` | `rgb`, `all`, or a list such as `1,2,6` or `D5,D6` | `rgb` | `rgb` is the RGB simulation, a false-colour composite of three infrared bands (R = D6 or D5, G = D4 or D3, B = D2 or D1); `all` averages the six detectors |
| `band` | `RGB`, `D1` ... `D6` | - | Alternative spelling of `det`; takes precedence when given |
| `group` | `exposure`, `day`, `visit` | `day` | One epoch per exposure, per UTC day, or per sky pass |
| `diff` | boolean | `false` | Subtract the median of all epochs (PNG and GIF endpoints) |

### Asynchronous jobs

Gathering the frames of a view is a background job. Endpoints that need the
frames accept `wait` (seconds): the request is held until the job is ready or
the time has passed.

- If the job is not ready in time, the answer is **HTTP 202** with
  `{"pending": true, "job": {...}}`. Repeat the request.
- A request may be held up to 6 s regardless of `wait` while the exposure search
  or the cache lookup of a previously loaded view is in progress.
- `/api/cube` and, by default, `/api/spectrum` do not wait for completion: they
  return what has arrived so far together with the job status.

The job object:

```json
{
  "id": "9cdfd6c58979",
  "status": "ready",
  "total": 358,
  "done": 358,
  "failed": 0,
  "frames": 358,
  "error": "",
  "elapsed_s": 0.4,
  "stats": {"search_s": 0.01}
}
```

| Field | Description |
| --- | --- |
| `status` | `queued`, `searching`, `fetching`, `ready` or `error` |
| `queue` | `null`, or `{"position": 3, "waiting": 8}` while the pipeline is busy with other views and this one waits its turn (`status` is then `queued`). Position 1 is next. A waiting view already holds its first few exposures, so `frames` may be above zero |
| `total` | Exposures found for the view |
| `done` | Exposures processed (from cache or archive) |
| `failed` | Exposures that could not be read after three attempts |
| `frames` | Exposures that intersect the view and produced a frame |
| `elapsed_s` | Wall time of the job |
| `stats` | Seconds and megabytes by phase: `search_s`, `wave_s`, `fetch_s`, `store_s`, `requests`, `mb`, `net_s` (summed over concurrent requests), `cpu_s`, `busy_s` (summed over worker processes). Keys are present only for phases that ran |

### Errors

Errors are JSON of the form `{"detail": "message"}`.

| Status | Meaning |
| --- | --- |
| 400 | Invalid input: unknown object name, malformed coordinates, invalid `det`, `group`, `cat` or `tile_id`, missing position |
| 404 | No SPHEREx data at this position (PNG and GIF endpoints) |
| 413 | The requested cube exceeds 40 million pixels |
| 422 | A parameter is outside its allowed range or of the wrong type (FastAPI validation) |
| 429 | This caller asks too much: too many requests, too many views being gathered at once, or the allowance for name lookups, catalogue queries or measurements is used up. `Retry-After` gives the seconds to wait |
| 502 | An upstream service failed: the data archive, the catalogue service, or the measurement database |
| 503 | The name services are unreachable, the measurement service has not started, or the server as a whole is full (views being gathered, measurement queue); the last case carries `Retry-After` |

## Endpoint summary

| Endpoint | Returns | Purpose |
| --- | --- | --- |
| [`/api/health`](#get-apihealth) | JSON | Liveness and instance identity |
| [`/api/settings`](#get-apisettings) | JSON | The limits in force and where each comes from |
| [`/api/featured`](#get-apifeatured) | JSON | The featured targets |
| [`/api/resolve`](#get-apiresolve) | JSON | Name or coordinates to a position |
| [`/api/exposures`](#get-apiexposures) | JSON | Every exposure covering a position |
| [`/api/view`](#get-apiview) | JSON | Start a view; job status |
| [`/api/tiles`](#get-apitiles) | JSON | Start a progressive field; stage and cell status |
| [`/api/cube`](#get-apicube) | binary | All epochs of a view for interactive display |
| [`/api/epochs`](#get-apiepochs) | JSON | Epoch metadata of a view |
| [`/api/frame.png`](#get-apiframepng) | PNG | One epoch as an image |
| [`/api/movie.gif`](#get-apimoviegif) | GIF | The whole time series as an animation |
| [`/api/spectrum`](#get-apispectrum) | JSON | Spectrum and light curve at the centre |
| [`/api/catalog`](#get-apicatalog) | JSON | Gaia DR3 or CatWISE2020 sources in the field |
| [`/api/object`](#get-apiobject) | JSON | What lies at a position and how far it is, from published catalogues |
| [`/api/tracks`](#get-apitracks) | JSON | Measured objects in the field |
| [`/api/measure`](#get-apimeasure) | JSON | Measure an object; job status and result |
| [`/api/measured`](#get-apimeasured) | JSON | Everything measured so far |
| [`/api/atlas/2mass/...`](#get-apiatlas2mass) | WebP or JPEG | One tile of the sky photograph behind the map |
| [`/api/atlas`](#get-apiatlas) | JSON | What the server keeps of the sky photograph |
| [`/api/movers`](#get-apimovers) | JSON | The log of moving objects found by the blind search |
| [`/api/movers/field`](#get-apimoversfield) | JSON | Moving objects of one progressive field; state of its search |
| [`/api/movers/{id}`](#get-apimoversid) | JSON | One moving object with its detections |
| [`POST /api/movers/{id}/vote`](#post-apimoversidvote) | JSON | A visitor's opinion: real motion or false alarm |
| `/` | HTML | The viewer; accepts the URL parameters in [viewer](viewer.md#url-state) |
| `/static/*` | files | Viewer assets |

## Service

### GET /api/health

```json
{"ok": true, "collections": ["spherex_dr1", "spherex_qr3"], "instance": "60-1794633151",
 "commit": "be4ffb476d50091e7f5ec2022ce636e0e81ec8cd", "memory": {...}}
```

`instance` identifies the web process (process id and start time). It must be
identical on successive calls; differing values mean more than one web process
is running, which the service does not support.

`commit` is the commit of the repository the service was built from (the host's
`RENDER_GIT_COMMIT`, or `SPHEREX_COMMIT`), `null` where neither is set. The
viewer's "Source code" links (credits note, About the data) go to that commit,
so a visitor gets the source of the version that is running.

`memory` is the memory the container really holds, in MB, read from its own
accounting (`sphereblink/memory.py`); it is `null` where the container has no
memory limit:

```json
{"limit_mb": 8589.9, "used_mb": 4210.5, "file_cache_mb": 3105.2, "free_mb": 4379.4,
 "working_set_mb": 6950.1, "anon_mb": 3800.4, "shmem_mb": 310.0, "peak_mb": 7900.3, "oom_kills": 0,
 "processes": [{"role": "web", "own_mb": 2100.3, "shared_mb": 290.1}, {"role": "worker", "own_mb": 410.0, "shared_mb": 120.5}]}
```

(Example values.) `used_mb` is what cannot be given back and is what can end
in the service being killed at `limit_mb`; `file_cache_mb` is the cache of
files read and written (the frame cache on disk above all), which the kernel
drops when the room is needed. A platform's memory graph plots about
`working_set_mb`, which counts the part of that cache in recent use, so it can
stand near the limit while `free_mb` is large. `oom_kills` counts the processes
killed for lack of memory since the container started. `processes` lists the
web process and the processes it started, largest first: `own_mb` is the
memory a process holds for itself, `shared_mb` the shared memory it has mapped
(frames travel between processes in shared memory, so several may count the
same megabytes).

### GET /api/settings

The limits in force: how many views and measurements the pipeline works on at
a time, and how much one caller may ask for. They are rows of the table
`settings` of the team database and can be changed while the server runs
([deployment](deployment.md#settings-in-the-database)); this endpoint is how
to see that a change arrived. No parameters; never cached.

```json
{
  "database": true, "read_seconds_ago": 12.4, "problem": null, "refresh_s": 30.0, "memory_cap_mb": 4900.0,
  "settings": {
    "active_views": {"value": 6, "from": "database", "default": 4, "min": 1, "max": 8, "note": null,
                     "what": "Views whose exposures are cut at the same time; the next ones wait in the queue"},
    "ram_budget_mb": {"value": 1500.0, "from": "corrected", "default": 2000.0, "min": 300.0, "max": 4000.0,
                      "note": "lowered to 1500: the frames kept, the frames being gathered and 250 MB for each measuring process may take 4900 MB together",
                      "what": "Memory (MB) for the frames of views already gathered; above it the views opened longest ago are reloaded from disk"}
  },
  "unknown_rows": []
}
```

| Field | Meaning |
| --- | --- |
| `database` | Whether the service has a database to read the table from |
| `read_seconds_ago` | When the table was read last; `null` if never since the start. It is read every `refresh_s` seconds |
| `problem` | Why the last reading failed (the kind of error only), or `null`. The values read last stay in force meanwhile |
| `memory_cap_mb` | What `ram_budget_mb`, `active_mb` and 250 MB for each of `measure_procs` may come to together |
| `settings.*.value` | The value in force |
| `settings.*.from` | `database`: the row as written; `corrected`: the row was outside its range or the memory rule lowered it (`note` says which); `default`: no row, the value of the service's environment |
| `settings.*.default`, `min`, `max` | The value without a row, and the range a row may set |
| `unknown_rows` | Rows of the table whose key the service does not know (a misspelt key) |

### GET /api/featured

The featured targets, for menus.

```json
[
  {"key": "barnard", "name": "Barnard's Star", "note": "Highest proper motion star (10.4 arcsec/yr)."},
  {"key": "eps-indi-b", "name": "Epsilon Indi B", "note": "Pair of brown dwarfs orbiting Epsilon Indi A."}
]
```

## Positions and exposures

### GET /api/resolve

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `q` | string | yes | Name or coordinates |

```json
{
  "name": "Barnard's Star",
  "ra": 269.4462878681711,
  "dec": 4.767916718333334,
  "ra_input": 269.452083,
  "dec_input": 4.693364,
  "pmra": -802.8,
  "pmdec": 10362.54,
  "epoch": 2000.0,
  "note": "Highest proper motion star (10.4 arcsec/yr).",
  "source": "featured"
}
```

| Field | Description |
| --- | --- |
| `ra`, `dec` | Position at the SPHEREx epoch (2025.9), after applying proper motion. This is the centre of the view |
| `ra_input`, `dec_input`, `epoch` | Catalogue position and its epoch (Julian year) |
| `pmra`, `pmdec` | Proper motion in mas/yr; `pmra` includes cos(dec) |
| `source` | `featured`, `coordinates`, `SIMBAD`, `NED` or `VizieR` |

This target object is embedded in the response of every view endpoint.

### GET /api/exposures

Parameters: `target` or `ra` and `dec`.

Every SPHEREx Level 2 exposure whose footprint contains the position, oldest first.

```json
{
  "target": {"...": "..."},
  "count": 358,
  "exposures": [
    {
      "obs_id": "2025W33_2C_0321_1",
      "detector": 3,
      "collection": "spherex_dr1",
      "mjd_start": 60902.864715,
      "mjd_end": 60902.866047,
      "wave_min_um": 1.6301,
      "wave_max_um": 2.4346,
      "s3_key": "dr1/level2/2025W33_2C/l2b-v27-2026-228/3/level2_2025W33_2C_0321_1D3_spx_l2b-v27-2026-228.fits",
      "access_url": "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/...",
      "s_region": "POLYGON ICRS 266.69 5.48 270.17 5.17 269.99 1.68 266.50 2.00 266.69 5.48",
      "ra_center": 268.3392384983133,
      "dec_center": 3.6055052194421444,
      "uid": "2025W33_2C_0321_1_D3",
      "mjd": 60902.865381
    }
  ]
}
```

`uid` (`obs_id` plus detector) identifies an exposure throughout the API and in
the measurement database. `s3_key` is the object key in the public bucket
`nasa-irsa-spherex`.

## Views

### GET /api/view

Parameters: view address, and `poll` (boolean, default `false`).

Starts gathering the frames of a view, or reports on a view already started.
Returns immediately. Poll until `job.status` is `ready`.

A view that failed is started again by this call, which is what opening it
anew should do. To watch a view you have opened, poll with `poll=true`: the
view is then reported as it is, and one that failed comes back with
`job.status` `error` and the reason in `job.error`. Without it every poll
after a failure would be answered with a new attempt, and the failure would
never be seen. A view the server no longer holds is started either way.

```json
{
  "target": {"name": "Barnard's Star", "ra": 269.4462878681711, "dec": 4.767916718333334, "...": "..."},
  "grid": {"ra": 269.4462878681711, "dec": 4.767916718333334, "npix": 157, "scale_arcsec": 3.075, "fov_arcmin": 8.046},
  "job": {"id": "9cdfd6c58979", "status": "ready", "total": 358, "done": 358, "failed": 0, "frames": 358, "error": "", "elapsed_s": 0.4, "stats": {"search_s": 0.01}}
}
```

`grid` describes the image grid of the view: centre, pixels per side, pixel
scale and the resulting exact field of view.

### GET /api/tiles

Starts a progressive field on the cell grid `icrs012-v1`, or reports on one
already started. Poll until `status` is `ready`. As with `/api/view`, poll
with `poll=true`: a field that failed is then reported with `status` `error`
instead of being started again.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target`, `ra`, `dec` | - | - | Position inside the seed cell |
| `tile_id` | string | - | Seed cell by identifier, for example `icrs012-v1-1716-0660`; overrides the position |
| `levels` | integer 0-3 | `3` | Rings of cells around the seed cell; 3 gives 7 x 7 cells |
| `neighbors` | integer 0-3 | - | Same as `levels`; takes precedence when given |

```json
{
  "target": {"...": "..."},
  "scheme": "icrs012-v1",
  "step_deg": 0.12,
  "levels": 3,
  "seed": {"tile_id": "icrs012-v1-1716-0660", "ra_c": 205.98, "dec_c": -10.74, "level": 0,
           "ra_min": 205.92, "ra_max": 206.04, "dec_min": -10.8, "dec_max": -10.68},
  "ready_level": -1,
  "status": "loading",
  "elapsed_s": 0.0,
  "stages": [
    {"level": 0, "tiles": 1,  "ra": 205.98, "dec": -10.74, "fov": 7.2,  "npix": 141, "status": "queued", "job": null},
    {"level": 1, "tiles": 9,  "ra": 205.98, "dec": -10.74, "fov": 21.6, "npix": 211, "status": "queued", "job": null},
    {"level": 3, "tiles": 49, "ra": 205.98, "dec": -10.74, "fov": 50.4, "npix": 493, "status": "queued", "job": null}
  ],
  "tiles": [
    {"tile_id": "icrs012-v1-1716-0660", "ra_c": 205.98, "dec_c": -10.74, "level": 0,
     "ra_min": 205.92, "ra_max": 206.04, "dec_min": -10.8, "dec_max": -10.68, "status": "loading"}
  ]
}
```

| Field | Description |
| --- | --- |
| `status` | `loading`, `ready` or `error` for the whole field |
| `ready_level` | Highest level whose stage has finished; -1 before the first |
| `stages[]` | One entry per loaded stage. `ra`, `dec` and `fov` address the stage as an ordinary view for `/api/cube`, `/api/frame.png`, `/api/movie.gif`, `/api/epochs` and `/api/spectrum`. `job` is the job object of the stage once it has started |
| `tiles[]` | Every cell of the block, centre first then ring by ring, with its corners and a status of `done`, `loading` or `queued` |

### GET /api/cube

All epochs of a view in one binary response, for interactive display. It does
not wait for the job to finish: while the job is running the cube contains the
epochs available so far.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| view address | | | |
| `det`, `band`, `group` | | `rgb`, -, `day` | See conventions |
| `bin` | integer 1-4 | `1` | Average `bin` x `bin` pixels (overview of a wide mosaic) |

Response: `application/octet-stream`, header `X-Job: <status>`,
`Cache-Control: no-store`. Returns 413 when epochs x planes x pixels exceeds
40 million; reduce the field of view, group by day or sky pass, select one
detector, or bin.

#### Binary cube format

```
offset 0        uint32, little-endian   N = length of the JSON block in bytes
offset 4        N bytes                 JSON metadata, UTF-8, padded with spaces so that 4 + N is even
offset 4 + N    uint16, little-endian   pixels in the order [epoch][plane][row][column]
```

- `plane` is present only for colour cubes (3 planes: R, G, B); grey cubes have one.
- Rows run from south to north; columns from east to west. To draw north up and
  east left, flip the rows.
- Pixel value 0 means "no data". Any other value `q` decodes to

  ```
  value = sinh(a_lo + (q - 1) / 65534 * (a_hi - a_lo))
  ```

  with `a_lo` and `a_hi` from `encoding`. The unit is the robust sky noise of a
  single exposure (sigma); colour planes are in white-balanced display units.

JSON metadata:

```json
{
  "target": {"...": "..."},
  "grid": {"ra": 269.446, "dec": 4.768, "npix": 157, "scale_arcsec": 3.075, "fov_arcmin": 8.046},
  "job": {"...": "..."},
  "encoding": {"a_lo": -0.4958639, "a_hi": 3.7789884, "n": 39, "planes": 3},
  "epochs": [{"...": "see /api/epochs"}]
}
```

With `bin` greater than 1, `grid.npix` and `grid.scale_arcsec` describe the
binned cube and `grid.native_npix` gives the size of the unbinned view.

A decoder in JavaScript:

```javascript
const buf = await (await fetch(url)).arrayBuffer();
const n = new DataView(buf).getUint32(0, true);
const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
const q = new Uint16Array(buf, 4 + n);
const {a_lo, a_hi} = meta.encoding, k = (a_hi - a_lo) / 65534;
const cube = Float32Array.from(q, (v) => (v === 0 ? NaN : Math.sinh(a_lo + (v - 1) * k)));
```

### GET /api/epochs

Parameters: view address, `det`, `band`, `group`, `wait` (0-60 s, default 0).

The epoch metadata of a view without the pixels.

```json
{
  "target": {"...": "..."},
  "grid": {"...": "..."},
  "job": {"...": "..."},
  "epochs": [
    {
      "channels": [
        {"color": "R", "detector": 6, "date": "2025-08-15", "borrowed": false},
        {"color": "G", "detector": 3, "date": "2025-08-15", "borrowed": false},
        {"color": "B", "detector": 2, "date": "2025-08-21", "borrowed": true}
      ],
      "label": "2025-08-15",
      "mjd": 60902.86687,
      "n_exposures": 6,
      "detectors": [3, 6],
      "coverage": 0.999,
      "wave_um": [2.3, 4.996],
      "flags_at_target": ["BLOOM", "DICHROIC", "NONFUNC", "OVERFLOW", "SOURCE", "SUR_ERROR", "TRANSIENT"],
      "exposures": ["2025W33_2C_0321_1_D3", "2025W33_2C_0321_1_D6", "2025W33_2C_0321_2_D3",
                    "2025W33_2C_0321_2_D6", "2025W33_2C_0321_3_D3", "2025W33_2C_0321_3_D6"]
    }
  ]
}
```

| Field | Description |
| --- | --- |
| `label` | UTC date, or `first .. last` for an epoch spanning several days |
| `mjd` | Mean MJD of the member exposures |
| `channels` | Colour epochs only: for each of R, G, B the detector and date it came from, and whether it was borrowed from another epoch |
| `coverage` | Fraction of pixels with data |
| `wave_um` | Minimum and maximum wavelength at the target among the member exposures |
| `flags_at_target` | SPHEREx quality flags set within the aperture at the target in any member exposure |
| `exposures` | `uid` of every member exposure |

### GET /api/frame.png

One epoch rendered as a PNG, annotated with the date, the detectors (an asterisk
marks a borrowed colour channel), the target name, a crosshair, and "N up, E left".

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| view address, `det`, `band`, `group`, `diff` | | | See conventions |
| `i` | integer | `-1` | Epoch index; negative values count from the end |
| `size` | integer 128-1024 | `480` | Image size in pixels |
| `wait` | float 0-120 | `55` | Seconds to wait for the job |

Response: `image/png`, `Cache-Control: public, max-age=3600`; 202 if pending;
404 if there are no data.

### GET /api/movie.gif

The whole time series as an animated GIF, with one contrast stretch for all
frames. Suitable for embedding in any page or message.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| view address, `det`, `band`, `group`, `diff` | | | See conventions |
| `fps` | float, up to 10 | `2` | Frames per second |
| `size` | integer 128-720 | `360` | Image size in pixels |
| `max_frames` | integer 2-200 | `60` | Longer series are thinned evenly in time |
| `wait` | float 0-120 | `55` | Seconds to wait for the job |

Response: `image/gif`, `Cache-Control: public, max-age=3600`; loops
indefinitely with a pause on the last frame; 202 if pending; 404 if there are
no data. Every frame carries the line `SPHEREx: NASA/JPL-Caltech/IPAC` (as
does `/api/frame.png`), and the full credit is stored in the file's comment
field, so the source stays with the picture wherever it is embedded.

## Measurements at the centre

### GET /api/spectrum

Spectrum and light curve at the centre of the view, one point per exposure.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| view address | | | |
| `wait` | float 0-120 | `0` | Seconds to wait for the job |
| `partial` | boolean | `true` | Return the points available so far. With `false`, the answer is 202 until the job is ready |

```json
{
  "target": {"...": "..."},
  "job": {"...": "..."},
  "units": {"flux": "uJy", "wave": "um"},
  "points": [
    {
      "wave_um": 0.7374,
      "bandwidth_um": 0.0176,
      "flux_ujy": 3245088.53,
      "flux_err_ujy": 649261.05,
      "method": "wings",
      "detector": 1,
      "mjd": 61277.25238,
      "uid": "2026W35_1A_0249_4_D1",
      "flags": ["BLOOM", "OVERFLOW", "SOURCE", "SUR_ERROR", "TRANSIENT"]
    }
  ]
}
```

| Field | Description |
| --- | --- |
| `wave_um`, `bandwidth_um` | Central wavelength and bandwidth of the filter slice at the target in this exposure |
| `flux_ujy`, `flux_err_ujy` | Flux density and its 1 sigma error in microjansky |
| `method` | `aperture`: sky-subtracted aperture of radius 2 native pixels. `wings`: pixels of the core are saturated and have no value, so the flux comes from the star's halo, with a 20-25 % systematic error included |
| `flags` | Quality flags in the aperture. `OVERFLOW`, `TRANSIENT` and `SUR_ERROR` on an `aperture` point do not make it a poor one: a source above about 0.1 Jy sets them itself, and the flagged pixels are part of the sum ([why](imaging-engine.md#flagged-pixels-in-the-aperture)). `PARTIAL` is added, not a flag of the archive, when a pixel at the rim of the aperture was left out of the sum. `EXTRAPOLATED` is added to wing fluxes above the calibrated range (error 50 %) |
| `mjd`, `detector`, `uid` | When and where the point was taken |

Points are sorted by wavelength. For a spectrum, plot `flux_ujy` against
`wave_um`; for the flux over time, against `mjd`, comparing points of equal
wavelength only (each exposure is at another wavelength).
For a complete spectrum, call with `wait=60` and check that `job.status` is `ready`.

### GET /api/catalog

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| view address | | | |
| `cat` | `gaia` or `catwise` | `gaia` | Gaia DR3 or CatWISE2020 |

The 250 brightest sources within 0.75 x `fov` of the centre, brightest first.

Gaia is read from the ESA Gaia archive and CatWISE from IRSA's cone search;
IRSA's TAP service is asked when the first service fails or does not answer in
25 s. The answer is stored per view. For the five featured targets the service
stores both catalogues at every field size the viewer offers shortly after it
starts, so those are always answered from the store. A `502` means both
services failed.

```json
{
  "cat": "gaia",
  "sources": [
    {"id": "4472832130942575872", "ra": 269.44850252544, "dec": 4.73942005111,
     "pmra": -801.551, "pmdec": 10362.394, "pm_snr": 288884.5,
     "mag": 8.193974, "parallax": 546.9759, "epoch": 2016.0}
  ]
}
```

| Field | Description |
| --- | --- |
| `ra`, `dec`, `epoch` | Position and the Julian year it refers to (2016.0 for Gaia; the mean observation epoch for CatWISE) |
| `pmra`, `pmdec` | Proper motion in mas/yr for both catalogues (CatWISE values are converted from arcsec/yr) |
| `pm_snr` | Total proper motion divided by its error. Use it to disregard the large but insignificant motions of faint CatWISE sources |
| `mag` | G magnitude (Gaia) or W2 magnitude (CatWISE) |
| `parallax` | mas (Gaia only) |
| `suspect` | CatWISE only, present and `true` when the catalogue's motion was judged an artifact of a bright star; `pmra`, `pmdec` and `pm_snr` are then 0 |

CatWISE splits the halo and diffraction spikes of a bright star into many
entries with large, confident and wrong proper motions (48 of the 250
brightest entries around Barnard's star). An entry that reports a fast motion
(above 150 mas/yr at `pm_snr` of 5 or more) is marked `suspect` when it is a
saturated star, lies in the halo of a bright entry, carries a CatWISE artifact
flag, or has four or more unrelated fast entries within 200 arcsec. Pieces of
one moving object (WISE 0855-0714 is listed four times along its path) count
once and stay.

To place a source on a frame taken in Julian year `t`:

```
ra(t)  = ra  + pmra  / 3.6e6 * (t - epoch) / cos(dec)
dec(t) = dec + pmdec / 3.6e6 * (t - epoch)
```

### GET /api/object

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` | string | | Object name or coordinates |
| `ra`, `dec` | degrees | | A position, instead of `target` |
| `second` | 0 or 1 | 0 | Where SIMBAD gave the redshift, also ask NED and put what it lists for the same object (within 3 arcsec) in `redshift.second`: `z`, `z_err`, `nature`, `name`, `sep_arcsec`, `source`, `bibcode`. The viewer asks for it when the lines of the SPHEREx spectrum disagree with the published value |

What lies at the position and how far it is. The values are published ones:
nothing is measured from the SPHEREx images. See
[distance and redshift](distance-redshift.md) for the rules of the lookup.

```json
{
  "target": {"name": "3C 273", "ra": 187.277916, "dec": 2.052389, "source": "SIMBAD"},
  "object": {"name": "3C 273", "otype": "BLL", "type": "BL Lac object (a blazar)",
             "sep_arcsec": 0.0, "sp_type": "", "morph_type": "E3", "source": "SIMBAD (CDS)"},
  "kind": "quasar",
  "distance": null,
  "redshift": {"z": 0.157568, "z_err": 0.0005, "nature": "spectroscopic", "quality": "C",
               "source": "SIMBAD (CDS)", "bibcode": "2022ApJS..261....2K",
               "velocity_km_s": null, "local": false,
               "cosmology": {"lookback_gyr": 2.0368, "age_then_gyr": 11.7501, "age_now_gyr": 13.787,
                             "comoving_mpc": 671.84, "comoving_gly": 2.1913, "luminosity_mpc": 777.7,
                             "cosmology": "Planck 2018 (flat ΛCDM, H0 = 67.66 km/s/Mpc, Ωm = 0.3111)"}},
  "radial_velocity": null,
  "measured_distance": null,
  "notes": [],
  "unavailable": [],
  "complete": true
}
```

| Field | Description |
| --- | --- |
| `kind` | `star`, `galaxy`, `quasar`, `other` or `unknown` (nothing catalogued there) |
| `object` | The catalogue entry taken as the object, how far it is from the position, and which catalogue named it |
| `distance` | Stars only: `pc`, `ly`, the range `pc_lo` to `pc_hi`, the parallax and its error in mas, `method`, `source`, `reference`. `uncertain` is true when the parallax is below 5 times its error |
| `redshift` | Galaxies and quasars: `z`, `z_err`, `nature` (spectroscopic or photometric), the catalogue's `quality` letter, `source`, `bibcode`. `local` is true below z = 0.01, where z is a velocity and not a distance; `cosmology` is what follows from z and names the model, and is null for a blueshift |
| `radial_velocity` | Stars: `km_s` with its `source` and `bibcode`. Where SIMBAD lists a velocity above 100 km/s that stands far from every other published one, `km_s` is the median of the `count` published measurements and `listed` holds the value and paper SIMBAD lists first |
| `measured_distance` | Galaxies with distances by other methods in SIMBAD: median `mpc`, `mpc_min`, `mpc_max`, `count`, `methods`, and the `newest` three with bibcodes |
| `notes` | Plain sentences about limits of this answer |
| `unavailable` | Catalogue services that did not answer |
| `complete` | False when `unavailable` is not empty: the answer is not stored then, and asking again later may add to it |

Status `400` for a missing position, `429` when the caller's allowance of
catalogue queries is used up, `502` when the lookup itself failed, `503` when
a name could not be resolved because the name services are down.

## Measured objects

### GET /api/tracks

Parameters: view address.

Objects measured within 0.75 x `fov` of the centre: pipeline results merged with
any measurements held in the team database.

```json
{
  "enabled": true,
  "sources": [
    {
      "name": "Barnard's Star",
      "kind": "star",
      "ra": 269.44850252544, "dec": 4.73942005111, "epoch": 2016.0,
      "pmra": -801.551, "pmdec": 10362.394, "parallax": 546.976,
      "gaia_source_id": "4472832130942575872", "catwise_id": null,
      "note": "Highest proper motion star (10.4 arcsec/yr).",
      "track": {
        "epoch": 2026.23709, "ra": 269.44619368, "dec": 4.76890604,
        "pmra": -992.21, "pmdec": 10327.03, "pmra_err": 471.8, "pmdec_err": 249.31,
        "n_detections": 86, "rms_arcsec": 0.388,
        "model": "linear motion, catalogue parallax",
        "summary": {"track": {"...": "..."}, "light_curve": {"...": "..."}, "stats": {"...": "..."},
                    "catalog": {"...": "..."}, "measured_at": 1794632085.6, "version": 2}
      },
      "detections": [
        {"obs_id": "2025W33_2C_0321_1", "detector": 6, "mjd": 60902.865381,
         "ra": 269.4460906, "dec": 4.7672754, "pos_err_arcsec": 0.092,
         "wave_um": 4.9234, "flux_ujy": 27476.81, "flux_err_ujy": 773.84,
         "flags": ["BLOOM", "OVERFLOW", "RING_FLUX", "SATURATED", "SOURCE", "SUR_ERROR", "TRANSIENT"],
         "method": "psf", "east": -0.498, "north": 0.416,
         "snr": 7.7, "n_exposures": 1, "used": true, "rel_flux": 0.9405}
      ]
    }
  ]
}
```

`enabled` is `false`, with an empty list, when the service has neither a
database nor any stored result.

| Object | Fields |
| --- | --- |
| Source | `ra`, `dec`, `epoch`, `pmra`, `pmdec`, `parallax`: the catalogue astrometry used for prediction. `kind`: `star`, `brown_dwarf`, `variable`, `moving`, `candidate` or `artifact` |
| `track` | Fitted position at `epoch` (Julian year), proper motion and errors in mas/yr, number of detections in the fit, RMS of the residuals. `summary.track` holds the full fit, `summary.light_curve` the brightness analysis. `null` if no track could be fitted |
| `detections[]` | Measured position in one exposure or stack. `east`, `north`: offset from the catalogue prediction in arcsec. `method`: `psf`, `wings` or `stack`. `used`: whether the detection entered the track fit. `rel_flux`: flux divided by the object's mean spectrum at that wavelength |

The flags `SATURATED` and `RING_FLUX` are added by the pipeline: the first marks
an exposure whose core is saturated (position only), the second a flux taken
from the ring outside the core. The example is the response served on
14 November 2026.

### GET /api/measure

Measures where an object is in every exposure and fits its motion. The first
call starts the job; repeat the call until `status` is `ready`. A stored result
is returned at once.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `target` or `ra`, `dec` | | | The object or position |
| `refresh` | boolean | `false` | Measure again even when a result is stored |
| `detections` | boolean | `true` | Include the per-exposure positions in the result |

```json
{
  "key": "wise-0855",
  "target": {"...": "..."},
  "job": {"status": "measuring", "name": "WISE 0855-0714", "exposures": 258, "elapsed_s": 12.4},
  "status": "measuring"
}
```

The example above shows the shape of a response while the job runs. `status`
is `queued`, `searching`, `measuring`, `ready` or `error` (with `job.error`).
A fixed number of objects is measured at a time (the setting `measure_procs`).
While others are ahead, `status` is `queued` and `job.queue` is
`{"position": 2, "waiting": 3}` (position 1 is next); otherwise `job.queue`
is `null`. Keep asking: the time in the queue does not count against the job.
When ready, `result` is present:

```json
{
  "key": "r-dra",
  "status": "ready",
  "result": {
    "name": "R Draconis",
    "kind": "variable",
    "ra": 248.1673, "dec": 66.7550,
    "note": "Mira variable, period ~246 days.",
    "target": {"name": "R Draconis", "ra": 248.1673783673, "dec": 66.7550067455, "epoch": 2016.0,
               "pmra": -20.643, "pmdec": 6.149, "parallax": 1.251, "mag": 6.37,
               "catalog": "Gaia DR3", "catalog_id": "1648328016354496384"},
    "track": {
      "model": "linear motion, catalogue parallax",
      "epoch": 2025.95929, "ra": 248.16731578, "dec": 66.75506245,
      "pmra": 153.52, "pmdec": 241.17, "pmra_err": 334.0, "pmdec_err": 425.93,
      "ra_err_arcsec": 0.114, "dec_err_arcsec": 0.201,
      "parallax_mas": 1.251, "parallax_fit_mas": null, "parallax_fit_err": null,
      "n_detections": 515, "n_rejected": 2, "n_passes": 3, "span_years": 1.166,
      "rms_arcsec": 0.922, "chi2_red": 7.46,
      "mjd_min": 60811.9565, "mjd_max": 61237.87241, "pm_fitted": true,
      "catalog_offset_east": 0.117, "catalog_offset_north": 0.139,
      "catalog_dpmra": 174.17, "catalog_dpmdec": 235.02
    },
    "light_curve": {
      "method": "wings", "n": 510,
      "passes": [
        {"mjd": 60845.155, "rel_flux": 1.4561, "err": 0.0219, "scatter": 0.2557, "n": 136},
        {"mjd": 61006.236, "rel_flux": 1.0003, "err": 0.0155, "scatter": 0.2204, "n": 202},
        {"mjd": 61198.695, "rel_flux": 0.7398, "err": 0.0136, "scatter": 0.1783, "n": 172}
      ],
      "days": [{"mjd": 60811.959, "rel_flux": 1.5614, "n": 8}],
      "amplitude": 1.968, "significance": 3.6, "floor": 0.12, "variable": true
    },
    "stats": {"stamps": 529, "passes": 3, "detections": 517, "single": 0, "wings": 517, "stacks": 0,
              "neighbours": 2, "pointing_east": -0.05, "pointing_north": -0.004,
              "pointing_scatter": 0.321, "pointing_stamps": 146,
              "exposures": 539, "failed": 0, "gaia": true, "seconds": 45.4, "in_database": true},
    "detections": [{"...": "as in /api/tracks, plus collection"}],
    "version": 2,
    "measured_at": 1794632400.0
  }
}
```

Field meanings are given in
[measurement pipeline](measurement-pipeline.md#7-result). Notable ones:

| Field | Description |
| --- | --- |
| `track.pm_fitted` | `false` when only one sky pass exists; the motion is then the catalogue value and its errors are `null` |
| `track.parallax_fit_mas` | Parallax fitted freely as a check (three or more passes); the main fit holds the catalogue parallax |
| `track.catalog_offset_*`, `catalog_dpm*` | Fitted minus catalogue: position in arcsec, proper motion in mas/yr |
| `light_curve.amplitude` | Brightest sky pass divided by the faintest |
| `light_curve.variable` | `true` at a significance of 3 or more |
| `stats.in_database` | Whether the result was written to the team database |

The values in this example are the result served on 14 November 2026, except
`measured_at` (a Unix time), which is illustrative.

### GET /api/measured

Every object the pipeline has measured, without per-exposure detections.

```json
{
  "version": 2,
  "database": true,
  "running": {},
  "results": [{"key": "barnard", "name": "Barnard's Star", "kind": "star", "track": {"...": "..."},
               "light_curve": {"...": "..."}, "stats": {"...": "..."}, "target": {"...": "..."}}]
}
```

`running` maps job keys to the status of measurements in progress. `database`
states whether a team database is configured.

## The sky photograph

### GET /api/atlas/2mass

`/api/atlas/2mass/Norder{k}/Dir{d}/Npix{n}.jpg`: one tile of the photograph
behind the zoomed-in sky map. It is the 2MASS colour atlas (J, H and K bands)
in the HiPS layout: order `k` from 0 to 9, tile number `n` below 12 x 4^k,
`d` = `n` rounded down to a multiple of 10000. The answer is the 512 pixel
JPEG exactly as the NASA/IPAC Infrared Science Archive serves it, so any HiPS
client can read tiles from this address.

A tile the server has on disk is answered from disk (`X-Atlas: kept`); any
other is fetched from the archive once and kept (`X-Atlas: fetched`). Tiles
may be cached for 30 days. `404`: no such tile. `503` with `Retry-After`: the
tile is not on disk and the archive does not answer. `429`: the caller's
allowance for tiles is used up ([security](security.md#request-limits)).

The same address ending in `.webp` gives the same tile as WebP, and that is
what the viewer downloads: the same 512 pixels in about a third of the bytes
(quality 80; [performance](performance.md#capacity-and-limits)). The server
makes the WebP from the JPEG the first time a tile is asked for and keeps it
beside the JPEG. Where it cannot be made at that moment (two are being made
already, or the JPEG cannot be read) the JPEG itself is answered, with
`Content-Type: image/jpeg` and a cache time of one hour. Headers and errors
are those of the `.jpg` address.

It is a backdrop, not SPHEREx data. Credit: 2MASS, University of
Massachusetts and IPAC/Caltech, funded by NASA and NSF; HiPS by CDS
(CNRS/Unistra, ODbL-1.0).

### GET /api/atlas

What the server keeps of the photograph.

```json
{
  "upstream": "https://irsa.ipac.caltech.edu/data/hips/CDS/2MASS/Color",
  "credit": "2MASS colour atlas (J, H, K): ...",
  "tiles": 16402, "bytes": 1652000000, "budget_bytes": 4000000000, "disk_free_bytes": 9100000000,
  "kept_orders": [0, 5], "kept_tiles": 16380, "kept_tiles_wanted": 16380,
  "counted": true, "seeding": null,
  "webp_tiles": 3100, "webp_bytes": 105000000,
  "from_disk": 5120, "fetched": 22, "dropped": 0,
  "webp_from_disk": 48000, "webp_made": 3100, "webp_busy": 0, "archive_resting": false
}
```

`kept_orders` are never deleted; `seeding` says which order is being filled
after a start and how many tiles are left. `webp_tiles` and `webp_bytes` are
the WebP copies kept beside their tiles; `bytes` counts them too.
`from_disk`, `fetched`, `dropped` and the three `webp_` counters count since
the server started: tiles answered as WebP from disk, WebP copies made, and
times the JPEG was answered because two copies were already being made. The numbers above are illustrative.

## Moving objects

The blind search and its log are described in [moving objects](moving-objects.md).
All objects are candidates found without prior knowledge; `known` tells which
of them are catalogued asteroids or comets.

### GET /api/movers

The log. Without a position the most recently changed objects come first.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ra`, `dec` | - | Centre of a cone, degrees. Give both or neither |
| `radius` | `1.0` | Radius of the cone, degrees (at most 30) |
| `ids` | - | Comma-separated object ids; returns exactly these (for example the ones a visitor follows) |
| `limit` | `200` | At most this many objects (1 to 1000) |
| `coverage` | `false` | Also return the sky cells searched so far |

```json
{
  "version": 4, "enabled": true, "total": 14,
  "movers": [
    {
      "id": "VS-60898-K7QD",
      "mjd0": 60898.170426, "ra0": 248.348339, "dec0": -16.659617,
      "v_east": 13.22, "v_north": -8.48, "a_east": 0.02514, "a_north": -0.01803,
      "rate_arcsec_hr": 15.7, "pa_deg": 122.7,
      "rms_arcsec": 8.9, "n_detections": 49, "n_times": 34, "n_visits": 11, "span_h": 81.5,
      "mjd_first": 60896.9, "mjd_last": 60900.3, "speed_class": "slow", "median_snr": 346.0,
      "covered": 65, "seen": 64, "seen_fraction": 0.985, "times_seen": 37, "times_covered": 37,
      "visits_seen": 11, "seen_by_visit": [3, 4, 4, 4, 4, 4, 2, 3, 4, 2, 3], "pieces": 2,
      "fields": ["..."],
      "known": {"checked": true, "by": "SkyBoT", "name": "5 Astraea", "kind": "asteroid",
                "source": "SkyBoT (IMCCE)", "sep_arcsec": 7.9, "vmag": 11.8, "rate_arcsec_hr": 15.6},
      "created": 1794650000.0, "updated": 1794650000.0,
      "votes": {"real": 2, "false": 0, "verdict": null}
    }
  ],
  "coverage": {"fields": 3, "cells": ["icrs012-v1-2069-0610"], "cell_deg": 0.12}
}
```

| Field | Meaning |
| --- | --- |
| `id` | `VS-<MJD of the first find>-<4 characters>`; stays the same when the object is found again |
| `mjd0`, `ra0`, `dec0` | Reference time and the position on the fitted path at that time |
| `v_east`, `v_north`, `rate_arcsec_hr`, `pa_deg` | Motion on the sky at `mjd0` in arcsec per hour; position angle from north through east |
| `a_east`, `a_north` | Change of the motion in arcsec per hour squared; 0 for a straight path. `t` hours from `mjd0` the object is `v_east * t + a_east * t^2 / 2` arcsec east of `ra0`, `dec0` (north alike) |
| `rms_arcsec` | Scatter of the detections around the path |
| `n_detections`, `n_times`, `n_visits`, `span_h` | Detections on the line, their distinct exposure times, visits (groups more than half an hour apart), hours from first to last |
| `speed_class` | `slow` (below 20 arcsec/h), `fast`, `very fast` (60 and more) |
| `covered`, `seen`, `times_covered`, `times_seen` | Exposures (and distinct exposure times) that cover the path, and in how many the object is seen at the predicted place |
| `visits_seen`, `seen_by_visit` | Visits in which it is seen at 2 or more exposure times, and the number of exposure times it is seen at in each visit; a path without a name needs 3 of the first kind or 4 visits in all |
| `pieces` | Present on a curved path joined from this many separately confirmed pieces |
| `known` | `{"checked": false}` when no service answered yet (they are asked again; a weak path is not listed until one answers); otherwise `by` (services asked) and `name` (`null`: no known object matches), with `kind`, `sep_arcsec`, `vmag`, `rate_arcsec_hr` of the match |
| `votes` | Counts of `real` and `false`; `verdict` is `real`, `false` or `disputed` from 3 votes on, else `null` |

### GET /api/movers/field

Objects in one progressive field and the state of its search. The field must
have been opened with [`/api/tiles`](#get-apitiles); the search starts by
itself when the field's frames are complete.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ra`, `dec` or `tile_id` | - | The seed cell of the field, as for `/api/tiles` |
| `levels` | `3` | Rings of the field (3 is 7 x 7 cells) |

`status` is `not_loaded` (the field's frames are not complete, or only an
earlier version of the search has gone through it), `queued`, `searching`,
`ready` or `error`. Poll every few seconds until `ready`.

When the field is `ready`, a second search for faint and slow objects may
still run ([moving-objects.md](moving-objects.md#the-second-search-faint-and-slow-objects)):
`job.stack` is then `searching`, later `ready` or `error`, and `movers` grows
when it ends. `stats.stack` is its record once it has run: `found`, `tracks`
(shown), `unnamed_dropped`, `waiting`, `seconds`, `passes`, `candidates`,
`rejected` (by reason), `skipped_windows`, `clear_share`, `version`, `movers`.

```json
{
  "field": {"ra": 248.36, "dec": -17.04, "fov": 50.4, "levels": 3, "seed": "icrs012-v1-2069-0608"},
  "status": "ready", "enabled": true,
  "job": {"status": "ready", "elapsed_s": 52.1, "stats": {"...": "..."}},
  "searched": 1794650000.0,
  "stats": {"frames": 713, "bad_frames": 131, "detections": 16882, "per_frame_median": 34, "masked_fraction": 0.227,
            "recurring": 3328, "linked": 14, "rejected": 7, "tracks": 7, "chance_tracks": 0.0,
            "chance_candidates": 0.0, "unnamed_dropped": 0, "waiting": 0,
            "seconds": 52.1, "movers": ["VS-60898-K7QD"]},
  "movers": []
}
```

`stats.chance_tracks` is the number of tracks the same search finds when the
exposure times are shuffled: what the field yields by chance.
`stats.chance_candidates` is the same with whole visits shuffled, counting
only paths with the evidence asked of one without a name.
`stats.unnamed_dropped` counts paths no catalogue has an object on that were
too weak to keep, `stats.waiting` those stored out of sight because no
catalogue service answered.

An object the second search found has `method: "stack"` and, in place of
the linker's evidence: `stack_snr` (significance of all its frames together),
`stack_strong` (7.5 sigma or more: shown without a name), `stack_frames`,
`stack_distance` (`distance_au`, `distance_min_au`, `distance_max_au`, `from`),
`stack_mag_ab` and `stack_flux_mjy` (at 1 micron), `stack_tier`. Its
`times_seen` is small by nature: most single frames show nothing. All keys are
in [shift-and-stack.md](shift-and-stack.md#interface).

### GET /api/movers/{id}

One object: every field of the log entry, plus `detections` (exposure id
`<obs_id>:D<detector>`, `mjd`, `ra`, `dec`, `snr`, `flux_mjy`, `npix`,
`detector`, `wave_um`) and `points`, the measurement at the predicted place in
every exposure that covers the path (`mjd`, `exposure`, `detector`, `wave_um`,
`ra`, `dec`, `snr`, `core_snr`, `seen`, `flux_mjy`, `on_static`, `counted`).
`snr` is the brightest pixel near the predicted place over the noise,
`core_snr` that pixel and its eight neighbours taken together, and `seen` the
verdict the search counts with (see [moving-objects.md](moving-objects.md)).
With `voter=<browser id>` the answer has `my_vote`. Unknown id: 404, also for
an object that an earlier version of the search stored and the current one
has not found again.

### POST /api/movers/{id}/vote

Body: `{"voter": "<browser id>", "vote": "real" | "false" | null}`. The browser
id is a random string of 16 to 64 letters, digits or dashes that the browser
makes up once and keeps. One vote per id and object; another vote replaces it,
`null` takes it back. Answer: `{"id": "...", "votes": {...}, "my_vote": "real"}`.

| Status | When |
| --- | --- |
| 400 | `voter` or `vote` malformed |
| 404 | No such object |
| 429 | More than 20 votes a minute from one caller, or more than 8 browser ids behind one address voting on the same object |

## Examples

Open the viewer on a target:

```
https://vanillasphere.com/?target=Barnard's Star&fov=8
https://vanillasphere.com/?ra=133.76010&dec=-7.24227&fov=8&det=5,6&group=visit
https://vanillasphere.com/?ra=206.005&dec=-10.763&levels=3
```

Embed a movie or a frame:

```html
<img src="https://vanillasphere.com/api/movie.gif?target=barnard&fov=8&group=day">
<img src="https://vanillasphere.com/api/frame.png?ra=269.44629&dec=4.76792&fov=8&i=-1">
```

Fetch a spectrum with Python:

```python
import httpx

r = httpx.get("https://vanillasphere.com/api/spectrum",
              params={"target": "R Dra", "wait": 60}, timeout=90)
points = r.json()["points"]
wave = [p["wave_um"] for p in points]
flux_mjy = [p["flux_ujy"] / 1000 for p in points]
```

Measure an object and wait for the result:

```python
import time, httpx

url = "https://vanillasphere.com/api/measure"
params = {"ra": 164.0925, "dec": 6.9953, "detections": "false"}
while True:
    d = httpx.get(url, params=params, timeout=30).json()
    if d["status"] in ("ready", "error"):
        break
    time.sleep(3)
print(d["result"]["track"])
```

Load a 7 x 7 field and fetch its mosaic:

```python
import time, httpx

base = "https://vanillasphere.com"
while True:
    f = httpx.get(base + "/api/tiles", params={"ra": 206.005, "dec": -10.763, "levels": 3}).json()
    if f["status"] != "loading":
        break
    time.sleep(2)
stage = f["stages"][-1]
gif = httpx.get(base + "/api/movie.gif",
                params={"ra": stage["ra"], "dec": stage["dec"], "fov": stage["fov"], "group": "visit"},
                timeout=120).content
```
