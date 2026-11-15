# Security and resilience

The product is a public, read-mostly web service: nobody logs in, and everything
it shows comes from public archives. This document states what is protected,
how, and what happens when a service it depends on fails. It describes `main`
as of 2026-11-14; the audit that led to these measures is summarised at the end.

## What there is to protect

| Asset | Threat | Protection |
| --- | --- | --- |
| Availability of the one server | A script floods the API, opens thousands of cold views, or holds every request thread | [Request limits](#request-limits), a pipeline queue in which callers take turns, bounded queues |
| The team measurement database | Unauthenticated writes, overwriting a stored result | The API writes only what its own pipeline measured, under a name the caller cannot choose; database roles and row level security |
| The disk cache | Being filled with junk so that useful views are evicted | Least-recently-used eviction; featured views pinned; job caps |
| Visitors' browsers | Script injection through names or data, framing by another site | Text is inserted as text; [response headers](#response-headers) |
| The upstream services (IRSA, CDS, ESA) | The product being used to hammer them | Lookups are cached and counted per caller |
| Secrets | Leaking the database password | One secret, held only by the host; none in the repository or the client |

There are no user accounts, sessions or cookies, so there is no personal data
and no session to steal.

## Request limits

All limits are per caller: one IPv4 address, or one IPv6 /64. The caller is the
address Cloudflare reports (`CF-Connecting-IP`), which a visitor cannot set;
`X-Forwarded-For` is used only when that header is absent. Requests from inside
the host's network (health checks) and the server's own background work are
never limited. The code is `sphereblink/guard.py`.

| Limit | Default | Setting | Refusal |
| --- | --- | --- | --- |
| API requests | 40 per second sustained, 1200 at once | `SPHEREX_RATE_REQUESTS_PER_S`, `SPHEREX_RATE_REQUESTS_BURST` | 429 |
| API requests open at once | 32 | `SPHEREX_MAX_OPEN_REQUESTS` | 429 |
| Views being gathered at once | 24 per caller; 96 in total | `SPHEREX_MAX_JOBS_PER_CLIENT`, `SPHEREX_MAX_JOBS` | 429; 503 |
| Progressive fields loading at once | 8 | `SPHEREX_MAX_FIELDS_PER_CLIENT` | 429 |
| Tiles of the sky photograph | 100 per second sustained, 3000 at once; counted apart from the API requests and not as open requests | `SPHEREX_RATE_TILES_PER_S`, `SPHEREX_RATE_TILES_BURST` | 429 |
| Sky tiles the server has to fetch from the archive | 1200 per minute; 8 fetches at once for all callers together | `SPHEREX_RATE_ATLAS_PER_MIN`, `SPHEREX_ATLAS_FETCHES` | 429; 503 |
| Names sent to the name services | 40 per minute | `SPHEREX_RATE_NAMES_PER_MIN` | 429 |
| Catalogue and archive queries that go out | 60 per minute | `SPHEREX_RATE_CATALOGS_PER_MIN` | 429 |
| New measurements | 6 at once, then 30 per hour | `SPHEREX_RATE_MEASURES_BURST`, `SPHEREX_RATE_MEASURES_PER_HOUR` | 429 |
| Measurements waiting or running | 12 in total | `SPHEREX_MAX_MEASURE_QUEUE` | 503 |
| Votes on moving objects | 20 per minute per caller; 8 browser ids per address and object | `SPHEREX_RATE_VOTES_PER_MIN`, `SPHEREX_MAX_VOTERS_PER_ADDRESS` | 429 |

Every limit of this table, except `SPHEREX_ATLAS_FETCHES` and the two for
votes, can be changed while the server runs: it is a row of the table
`settings` of the team database (`rate_requests_per_s` for `SPHEREX_RATE_REQUESTS_PER_S`, and so
on), and the environment variable is its value without a row. A row can
neither remove a limit nor set it outside the range written in the code; see
[deployment](deployment.md#settings-in-the-database).

The numbers are far above what a person needs, and above what a classroom
behind one address needs; they exist to stop a script. Only work that really
starts is counted: a view that exists, a name already resolved, a catalogue
already stored or a measurement already made costs nothing. A refusal carries
`Retry-After`, and the viewer waits that long and asks again.

Limits alone would still let one caller with 24 cold views delay everyone, so
the pipeline works on a limited number of views at a time (four, holding at
most 2400 MB of frames between them, in production) and the others wait in
a queue in which the callers take turns (see
[architecture](architecture.md#concurrency-model)): the first view of every
caller comes before anybody's second one. Measurements wait in a queue of
their own, first come, first served.

The static page, its scripts and `/api/health` are not limited.

## Response headers

Added to every response by `guard.Guard`:

| Header | Value | Purpose |
| --- | --- | --- |
| `Content-Security-Policy` (pages) | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://irsa.ipac.caltech.edu; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` | Only the site's own scripts run; no other site can frame the viewer. The one address beyond the site's own is the NASA/IPAC archive, for pictures only: the tiles of the sky photograph behind the zoomed-in map |
| `Content-Security-Policy` (data, pictures) | `default-src 'none'` | A response opened directly can run nothing |
| `X-Content-Type-Options` | `nosniff` | Browsers keep to the declared content type |
| `X-Frame-Options` (pages) | `SAMEORIGIN` | As `frame-ancestors`, for older browsers |
| `Strict-Transport-Security` | `max-age=31536000` | HTTPS only |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Other sites do not learn which view was open |
| `Permissions-Policy` | camera, microphone, geolocation, payment, USB off | The viewer needs none of them |
| `Cross-Origin-Resource-Policy` (data, pictures) | `cross-origin` | Pictures and data may be embedded elsewhere, as intended |

### Framing by other sites

A page shown inside a frame of another site can be covered with a decoy, so
that a visitor who clicks the decoy clicks the page (clickjacking). No page
of this site may be framed by another site:

- every answer that is a page (`/`, `/static/index.html`, `/docs`, `/redoc`)
  carries `frame-ancestors 'self'` in its policy and `X-Frame-Options:
  SAMEORIGIN`. Current browsers obey the first, older ones the second;
- pictures and data carry neither. A GIF, PNG or JSON answer of `/api/` is
  made to be used elsewhere, in an `<img>` or by `fetch`; the two headers
  that allow this are `Cross-Origin-Resource-Policy: cross-origin` and the
  open CORS below. To show a view on another page, embed its GIF or PNG and
  link to the view; the viewer itself cannot be put in an `<iframe>` there;
- a `304 Not Modified` answer gets neither policy. It has no content type,
  and a browser lays its headers over the ones it kept: with the policy of
  data it would take the page's policy, and `frame-ancestors` with it, away;
- the dev site is static, so `render.dev.yaml` sets the same: `X-Frame-Options`
  on every path, the page's policy on `/` and `/index.html` (a static site
  answers both with the page).

The viewer has no frame of its own, so `'self'` is not used today; it is
there so that a page of the site may show another one if that is ever wanted.

Tests: `test_no_other_site_can_frame_a_page_and_pictures_stay_free`,
`test_not_modified_does_not_replace_the_policy_of_the_page` and the dev site
test in `tests/test_guard.py`.

Inline style attributes are allowed because the viewer draws colour swatches
with them; there is no inline script. The interactive API pages (`/docs`,
`/redoc`) come from FastAPI and load Swagger UI from jsDelivr, so they get a
policy of their own that allows that host.

CORS is open to any origin for `GET` and `HEAD` without credentials. Every
endpoint is `GET`; nothing else is accepted.

## Input checks

- `ra` and `dec` are checked as numbers: finite, `0 <= ra < 360`,
  `-90 <= dec <= 90`. Before 2026-11-14 the numbers were written out as text
  and parsed again, so `nan`, `inf` and values below 0.0001 (written `1e-05`)
  were sent to the name services as if they were names, at 4-6 s each.
- An object name is at most 80 printable characters, must contain a letter or a
  digit, and may not contain `< > { } \ " ; = | ^ $ %` or a backtick. It is
  URL-encoded for Sesame and SQL-quoted for SIMBAD.
- A display label given together with `ra` and `dec` is reduced to one line of
  printable text of at most 80 characters. The viewer inserts names as text,
  never as HTML.
- `/api/measure` ignores that label. A position is measured and stored under a
  name derived from the position (or from the Gaia star found there). Before
  2026-11-14, a request with the coordinates of one place and the name of a
  featured target stored a measurement of that place under the target's name,
  replacing the real one.
- `det`, `group`, `cat`, `tile_id`, `fov`, `size`, `fps`, `wait`, `bin` and
  `levels` are validated against fixed sets or ranges. Catalogue queries are
  built from numbers only. A cube above 40 million pixels is refused (413).
- Errors are JSON with a short message. Stack traces are never returned.

## Database

- The Postgres database (Supabase) has row level security on `sources`,
  `detections` and `tracks`. There is no policy for the public `anon` and
  `authenticated` roles, so the public REST interface can read and write
  nothing; `sources_near` cannot be executed by them. Checked on 2026-11-14:
  the Supabase security advisor reports no finding.
- The web service connects as `blink_service` (read and write on the three
  tables, no other rights). All statements are parameterised.
- The table `settings` (the limits changed while the server runs) has row
  level security too and one policy: `blink_service` may read it. The service
  cannot write it, so nothing that reaches the service can change a limit;
  only the owner of the database can. What a row may set is bounded in the
  code whatever the table says
  ([deployment](deployment.md#settings-in-the-database)), and
  `/api/settings` shows the values, never the address of the database.
- The password exists only in the host's `DATABASE_URL`. A scan of the whole
  git history (68 commits) on 2026-11-14 found no secret, and the client
  contains no key. The scan was repeated on 2026-11-15, before the repository
  was made public, over all 343 commits of every branch and pull request and
  over the text of the 134 pull requests and their comments: no key, token,
  connection string, host identifier or `.env` file was ever committed.

## When an external service fails

| Service | Used for | What happens when it fails |
| --- | --- | --- |
| AWS S3 bucket `nasa-irsa-spherex` | Reading image files | The same byte ranges are read from IRSA's own copy. Measured 2026-11-14 with the bucket unreachable: a cold 8 arcmin view loaded in 52 s instead of 9 s. Views already on disk are not affected |
| IRSA SIA (exposure search) | Finding the exposures of a position | The stored list of that position is used, whatever its age. A position never opened before cannot be opened until the archive answers |
| IRSA as a whole | Search and second copy of the files | Everything already on disk still opens, including the 8 arcmin views of the five featured targets, which are fetched at start and never evicted |
| CDS Sesame | Object names | Second host (Harvard), then SIMBAD TAP on two hosts, then the answer stored on disk (kept 30 days, used at any age when no service answers). Coordinates and the featured targets need no service |
| IRSA HiPS tiles (2MASS atlas) | The photograph behind the zoomed-in sky map | The server keeps every tile it has fetched, and the whole sky at orders 0-6, on its disk: those are shown without the archive. A tile that is not on disk is refused at once while the archive is down (after five failures in a row it is left alone for 30 s). If the server itself cannot give a tile, the browser asks the archive directly; with both away the drawn map stays as it is. Nothing else depends on it |
| ESA Gaia archive, IRSA Gator | Catalogue overlays | Each catalogue has two services; the overlays of the featured targets are stored at start. See [data and sources](data-sources.md#external-services) |
| Supabase Postgres | Team measurements | The service's own results are also in a local store and are served from there |
| The server itself (Render) | Everything | One instance: a deploy or a crash interrupts the site. The server now stops at once instead of after 30 s, which leaves the 27 s the host needs to start the new instance (measured 2026-11-14); the viewer retries by itself, and a worker process that dies is replaced. A second server was considered on 2026-11-14 and not taken |

## The audit of 2026-11-14

| Finding | Severity | State |
| --- | --- | --- |
| A measurement of any place could be stored under a featured target's name (`/api/measure?ra=..&dec=..&target=..&refresh=true`), replacing its track | High | Fixed |
| No request limit of any kind; each new position started a thread, an archive search and about 0.5 GB of reads | High | Fixed |
| 40 request threads, most endpoints able to wait up to 120 s: 40 slow requests stopped the API | High | Fixed (160 threads, 32 open requests per caller, page and health check off the pool) |
| `nan`, `inf` and tiny coordinates went to the name services | Medium | Fixed |
| No security headers (CSP, HSTS, nosniff, framing) | Medium | Fixed |
| CORS allowed `POST` and any request header | Low | Fixed |
| Dependencies unpinned (`>=`): each deploy installed the newest release | Medium | Fixed (exact versions) |
| A worker process killed by the system left the pool broken until a restart | Medium | Fixed |
| Disk cache evicted by fetch time, so the most used views went first | Medium | Fixed |
| Table privileges of `anon` and `authenticated` in Postgres are the Supabase defaults; row level security is what closes them | Low | Open: revoking them is defence in depth and needs the database owner |
| Anyone may ask for a measurement of any position, and the result is written to the team database | Low | Open by design; now rate limited and queue bounded |
| `/docs` loads Swagger UI from a CDN without an integrity hash | Low | Open (FastAPI default) |
