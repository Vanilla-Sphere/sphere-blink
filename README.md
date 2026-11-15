<!--
  The pictures of this page are named by the commit that holds them, through the jsDelivr mirror of GitHub
  (cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@<commit>/...), and not by a relative path. GitHub then serves
  them from its own image cache. A relative path is fetched from raw.githubusercontent.com, which answered
  "503 Backend.max_conn reached" for some or all of the pictures in parts of Asia on 2026-11-15.
  To change a picture: commit it, then replace the commit id everywhere in this file.
  The banner, the numbers and the buttons are drawn by scripts/build_readme_art.py.
-->

<a id="top"></a>

<div align="center">

<a href="https://vanillasphere.com"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/banner.svg" alt="Vanilla Sphere" width="100%"></a>

### Watch any patch of the infrared sky change over time

Every NASA SPHEREx image of any position, played as a movie in seconds,<br>
with a spectrum, measured motion and a blind search for things that move.

<img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/award.svg" alt="Second prize at the Hà Nội local event of the 2026 NASA Space Apps Challenge, challenge Planet X and SPHEREx" height="72">

<a href="https://vanillasphere.com"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-live.svg" alt="Open the live site" height="38"></a>
<a href="#what-it-found"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-found.svg" alt="See what it found" height="38"></a>
<a href="https://vanillasphere.com/docs"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-api.svg" alt="API explorer" height="38"></a>
<a href="docs/README.md"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-docs.svg" alt="Documentation" height="38"></a>
<a href="docs/science/README.md"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-science.svg" alt="Science and algorithms" height="38"></a>

[![Live site](https://img.shields.io/badge/live-vanillasphere.com-b58afb?style=flat-square)](https://vanillasphere.com)
[![checks](https://img.shields.io/github/actions/workflow/status/Vanilla-Sphere/sphere-blink/checks.yml?branch=main&label=tests&style=flat-square)](https://github.com/Vanilla-Sphere/sphere-blink/actions/workflows/checks.yml)
[![UI tests](https://img.shields.io/github/actions/workflow/status/Vanilla-Sphere/sphere-blink/ui-tests.yml?branch=main&label=ui%20tests&style=flat-square)](https://github.com/Vanilla-Sphere/sphere-blink/actions/workflows/ui-tests.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-e9b452?style=flat-square)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab?style=flat-square&logo=python&logoColor=white)](requirements.txt)
[![Stars](https://img.shields.io/github/stars/Vanilla-Sphere/sphere-blink?style=flat-square&logo=github&label=stars&color=e9b452)](https://github.com/Vanilla-Sphere/sphere-blink/stargazers)
[![Forks](https://img.shields.io/github/forks/Vanilla-Sphere/sphere-blink?style=flat-square&logo=github&label=forks&color=b58afb)](https://github.com/Vanilla-Sphere/sphere-blink/forks)
<br>
[![NASA Space Apps 2026](https://img.shields.io/badge/NASA%20Space%20Apps%202026-Planet%20X%20and%20SPHEREx-0b3d91?style=flat-square&logo=nasa&logoColor=white)](https://www.spaceappschallenge.org/)
[![Last commit](https://img.shields.io/github/last-commit/Vanilla-Sphere/sphere-blink/main?style=flat-square&label=last%20commit&color=b58afb)](https://github.com/Vanilla-Sphere/sphere-blink/commits/main)
[![No AI inside](https://img.shields.io/badge/in%20the%20product-no%20AI%2C%20only%20arithmetic-b58afb?style=flat-square)](#use-of-ai-tools)

<a href="https://vanillasphere.com">
  <img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/sky-map.webp" alt="The start screen: a map of the whole sky; click anywhere to open that patch in SPHEREx data" width="100%">
</a>

</div>

NASA's SPHEREx mission images the whole sky again and again in 102 infrared
colours between 0.75 and 5.0 µm. Vanilla Sphere turns that archive into
something anyone can explore: click a point of the sky map, or type
coordinates or a name, and the browser plays every SPHEREx image of that
patch in time order. Nothing is downloaded in advance. Built by team Vanilla
Sphere for the 2026 NASA Space Apps Challenge, "Planet X and SPHEREx", where
it took second prize at the Hà Nội local event.

The whole product is open source under the [AGPL-3.0](#license): the engine,
the viewer, the tests and the design documentation are in this repository,
with their full history since the first commit of the hackathon
(2026-11-14). The UI test suite is in
[sphere-blink-ui-tests](https://github.com/Vanilla-Sphere/sphere-blink-ui-tests).

> [!TIP]
> Nothing to install and no account: open [vanillasphere.com/sky](https://vanillasphere.com/sky) and click anywhere on the sky.
> Space plays and pauses the movie, the arrow keys step through its frames.

<div align="center">
  <a href="#what-it-found"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/numbers.svg" alt="102 colours from 0.75 to 5.0 µm. 3I/ATLAS found by a blind search at 99 arcsec per hour. Pluto at 279 sigma by shift-and-stack. About 10 s to a new view, under 1 s the next time. 0 tracks by chance in shuffled searches." width="100%"></a>
</div>

## Contents

- [What it found](#what-it-found)
- [A tour](#a-tour)
- [What it does](#what-it-does)
- [One URL, one picture](#one-url-one-picture)
- [How it works](#how-it-works)
- [Run it](#run-it)
- [Measurements](#measurements)
- [Documentation](#documentation)
- [Data and credits](#data-and-credits)
- [Contributing](#contributing)
- [Use of AI tools](#use-of-ai-tools)
- [License](#license)

## What it found

Nobody told the software where to look. Every result below comes out of the
public SPHEREx images by documented, deterministic arithmetic, and each one
was checked against an independent source.

| | Result | Checked against |
| --- | --- | --- |
| **An interstellar comet** | The blind search picks up 3I/ATLAS crossing a field at 99 arcsec per hour, in 15 exposures of 7-8 August 2025. | The positions NASA/JPL predicts: the track stays 3.3-3.6 arcsec away, half of one SPHEREx pixel. [Moving objects](docs/moving-objects.md) |
| **Pluto, Makemake, Orcus** | Too slow for the first search, and all but Pluto too faint for a single exposure. Adding the exposures along thousands of trial paths (shift-and-stack) brings out six distant bodies: Pluto at 279 sigma, Makemake at 17, Haumea, Chiron, Orcus and Eris. | NASA's ephemerides: every one is placed to better than a pixel, Pluto to 0.2 arcsec. [Shift-and-stack](docs/shift-and-stack.md) |
| **A wrong catalogue redshift** | SIMBAD lists quasar SDSS J1528+3637 at z = 4.344. The lines of its SPHEREx spectrum stand where z = 2.41 puts them, and the viewer says so. | The SDSS quasar catalogue DR16Q and NED: 2.413. [Distance and redshift](docs/distance-redshift.md) |
| **No false alarms** | Three fields along the path of 3I/ATLAS gave 14 tracks. Every one is a known comet or asteroid. | The same searches on shuffled exposure times: 0 tracks by chance. [Moving objects](docs/moving-objects.md#what-it-was-checked-against) · [How much the search finds](docs/mover-completeness.md) |
| **1.3-1.9 MB instead of 65 MB** | A SPHEREx file is about 65 MB. The server reads only the bytes of the cutout with HTTP range requests, so a new 8 arcmin view of about 350 exposures is on screen in about 10 s, and in under 1 s the next time. | A cut from the full download: the pixels are identical. [Performance](docs/performance.md) |

<div align="center">
  <img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/3i-atlas-poster.webp" alt="It caught 3I/ATLAS: one real SPHEREx exposure with the comet in the crosshair" width="100%">
</div>

## A tour

Every picture here is a real view, and each button opens one on the live site.

<div align="center">

<a href="https://vanillasphere.com/sky?ra=248.34&amp;dec=-16.98&amp;levels=3&amp;mover=VS-60894-B5F6&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-3i-atlas.svg" alt="3I/ATLAS, interstellar comet" height="38"></a>
<a href="https://vanillasphere.com/sky?ra=304.14&amp;dec=-23.58&amp;levels=3&amp;mover=VS-60951-4LN9&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-pluto.svg" alt="Pluto, found by stacking" height="38"></a>
<a href="https://vanillasphere.com/sky?ra=200.70&amp;dec=21.06&amp;levels=3&amp;mover=VS-61191-X387&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-makemake.svg" alt="Makemake, dwarf planet" height="38"></a>
<a href="https://vanillasphere.com/sky?target=barnard"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-barnard.svg" alt="Barnard's Star, fastest star in the sky" height="38"></a>
<a href="https://vanillasphere.com/sky?target=eps-indi-b&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-eps-indi-b.svg" alt="Epsilon Indi B, brown dwarf spectrum" height="38"></a>
<a href="https://vanillasphere.com/sky?target=r-dra&amp;plot=lc&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-r-dra.svg" alt="R Draconis, a star that faded" height="38"></a>
<a href="https://vanillasphere.com/sky?ra=232.1947&amp;dec=36.631&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-quasar.svg" alt="SDSS J1528+3637, quasar redshift" height="38"></a>
<a href="https://vanillasphere.com/sky?target=wise-0855&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/go-wise-0855.svg" alt="WISE 0855, coldest brown dwarf" height="38"></a>

</div>

<table>
<tr>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?ra=248.34&amp;dec=-16.98&amp;levels=3&amp;mover=VS-60894-B5F6&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/3i-atlas-field.webp" alt="A 7 x 7 cell field with the track of 3I/ATLAS"></a>
<b>A field of 49 cells, and what moves in it.</b> The view grows ring by ring to 0.84 degrees. A blind search links what moved between exposures; the line is the track of comet 3I/ATLAS.
</td>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?ra=248.34&amp;dec=-16.98&amp;levels=3&amp;mover=VS-60894-B5F6&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/3i-atlas-exposures.webp" alt="3I/ATLAS in fifteen single exposures"></a>
<b>The evidence, exposure by exposure.</b> Each pair is centred where JPL predicts the comet: the exposure as taken, and the same with the static sky subtracted.
</td>
</tr>
<tr>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?ra=304.14&amp;dec=-23.58&amp;levels=3&amp;mover=VS-60951-4LN9&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/pluto-stack.webp" alt="Pluto found by shift-and-stack"></a>
<b>Pluto, found by adding exposures.</b> At 0.2 arcsec per hour it does not move enough for the first search. The stack of 43 exposures along its path shows it at 279 sigma.
</td>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?target=eps-indi-b&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/spectrum-reading.webp" alt="The spectrum of Epsilon Indi B with its preliminary reading"></a>
<b>A spectrum, and a first reading of it.</b> Every exposure sees the target at another wavelength. For the brown dwarf Epsilon Indi B the viewer names the water and methane bands by fixed, cited rules.
</td>
</tr>
<tr>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?target=r-dra&amp;plot=lc&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/over-time.webp" alt="R Draconis over time"></a>
<b>Brightness over time.</b> The Mira variable R Draconis faded by a factor of two between June 2025 and June 2026, band by band.
</td>
<td width="50%" valign="top">
<a href="https://vanillasphere.com/sky?ra=232.1947&amp;dec=36.631&amp;ui=adv"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/redshift.webp" alt="The redshift of a quasar measured from its lines"></a>
<b>Redshift from the lines.</b> For a quasar the viewer measures the redshift from its emission lines, gives the distance and look-back time, and says when a catalogue disagrees.
</td>
</tr>
</table>

<table>
<tr>
<td width="38%" valign="top">
<a href="https://vanillasphere.com"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/discoveries.webp" alt="The Discoveries log"></a>
</td>
<td valign="top">

**The Discoveries log.** Every moving object found as people open wide
fields is kept, named from the catalogues of known bodies (IMCCE SkyBoT,
NASA/JPL), and can be voted real or false.

The search is told nothing in advance. So far everything it has reported
is a known comet, asteroid or trans-Neptunian object, which is the result
one should expect from a search that works: an unknown one would have to
pass the same tests.

</td>
</tr>
</table>

## What it does

- **Any coordinates, on demand.** The server finds every SPHEREx Level 2
  exposure covering the position (IRSA SIA) and reads only the bytes it needs
  from the public AWS Open Data mirror.
- **Progressive fields, 7 x 7 cells.** The sky is addressed by the fixed
  0.12 degree cells of the team's measurement grid (`icrs012-v1-XXXX-YYYY`).
  A field starts at the cell of the requested position and grows ring by
  ring: the cell is on screen after seconds, its 3 x 3 neighbourhood next,
  then the whole 7 x 7 block (0.84 degrees, about 700 exposures) as one
  false-colour mosaic.
- **Blink movie** with a shared contrast stretch, time slider, keyboard
  control, and a timeline of the real observation dates. Frames can be
  stacked per UTC day or per six-month sky pass, or shown exposure by
  exposure, in any of the six detector bands.
- **Image math**: subtract the median of all frames (static stars vanish),
  subtract a chosen reference epoch, or add everything together.
- **Moving objects, found blind.** A linker for what moves between exposures
  and a shift-and-stack search for what is too faint and slow for it; names
  from the catalogues of known bodies; a public log.
- **Spectrum on the fly**, 0.75-5.0 µm, with a preliminary reading written by
  fixed rules in the browser (`web/insight.js`) from a cited line catalogue
  (`web/linelist.js`): where the curve peaks, which bands and lines stand at
  their reference wavelengths, which kinds of object give such a curve, and
  how sure it is.
- **Over time**: the flux at every visit, band by band, with a line saying
  whether the object changed.
- **Distance and redshift**: published distances of stars, redshifts of
  galaxies, and a redshift measured from the lines of the SPHEREx spectrum
  itself (`web/redshift.js`).
- **Bright stars too.** A star brighter than about K = 9 saturates the
  detector core. Those are measured from the star's halo instead, with a
  calibration fitted to 81 2MASS stars: within 20-25 % of 2MASS from 0.15 to
  20 Jy.
- **Measures by itself.** Position on the native detector pixels of every
  exposure, proper motion with parallax, and a light curve, for any object;
  see [Measurements](#measurements).
- **Overlays**: Gaia DR3 and CatWISE2020 sources, moved by their proper
  motion to the date of each frame. Quality flags of the pixels under the
  target are shown for every frame.
- **Simple and Advanced modes**, on a phone or a desktop.
- **No AI inside.** No machine-learning model runs in the product. Every
  image is NASA SPHEREx data processed by documented arithmetic.

## One URL, one picture

Every view is an address, and every picture and measurement has an endpoint,
so anything can be linked, embedded or fetched by code. This movie is one
request, `GET /api/movie.gif?target=barnard&fov=8&group=visit`: Barnard's
Star in six visits over thirteen months. The field stars stay where they are;
Barnard's Star, the fastest-moving star in the sky, creeps north by about
10 arcsec per year.

<div align="center">
  <img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/barnard-blink.gif" alt="Barnard's Star in six SPHEREx visits, as returned by /api/movie.gif" width="420">
</div>

```
/sky?target=Barnard's Star&fov=8
/sky?ra=133.76010&dec=-7.24227&fov=8&det=5,6&group=visit
/api/movie.gif?target=barnard&fov=8&group=day
/api/frame.png?ra=269.44629&dec=4.76792&fov=8&i=-1
/api/spectrum?target=R Dra&wait=60
/sky?ra=206.005&dec=-10.763&levels=3              progressive field, 7 x 7 cells
/api/tiles?ra=206.005&dec=-10.763&levels=3        its cells, stages and progress
/api/measure?target=WISE 0855                     positions, fitted motion, light curve
/api/measured                                     everything measured so far
```

The [API reference](docs/api-reference.md) lists every endpoint; the
interactive explorer is served at [`/docs`](https://vanillasphere.com/docs).
CORS is open for `GET`, so a page anywhere can use the pictures and the data.

## How it works

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/img/diagrams/imaging-pipeline-dark.png">
    <img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/img/diagrams/imaging-pipeline.png" alt="The imaging pipeline: request, grid, exposure search, range reads, clean and resample, frame cache, epochs, colour and encode" width="100%">
  </picture>
</div>

```
browser ──> FastAPI ──> IRSA SIA v2            which exposures cover (RA, Dec)?
                   └──> s3://nasa-irsa-spherex  4 range requests in 3 steps:
                          1. IMAGE header (WCS)
                          2. the pixel rows of the cutout + FLAGS row index
                          3. the RICE-compressed FLAGS rows
            mask bad pixels -> subtract sky -> resample to a north-up grid
            -> SQLite cache -> epochs (day / sky pass / exposure) -> browser
```

The viewer is plain HTML, CSS and JavaScript with no build step and no
framework. The server is one FastAPI process with its own worker processes.
The architecture, with fifteen diagrams, is in
[docs/architecture.md](docs/architecture.md).

<div align="center">

[![Python 3.12](https://img.shields.io/badge/Python%203.12-17121f?style=for-the-badge&logo=python&logoColor=b58afb)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-17121f?style=for-the-badge&logo=fastapi&logoColor=e9b452)](https://fastapi.tiangolo.com/)
[![NumPy](https://img.shields.io/badge/NumPy-17121f?style=for-the-badge&logo=numpy&logoColor=b58afb)](https://numpy.org/)
[![SciPy](https://img.shields.io/badge/SciPy-17121f?style=for-the-badge&logo=scipy&logoColor=e9b452)](https://scipy.org/)
[![Astropy](https://img.shields.io/badge/Astropy-17121f?style=for-the-badge)](https://www.astropy.org/)
[![SQLite](https://img.shields.io/badge/SQLite-17121f?style=for-the-badge&logo=sqlite&logoColor=b58afb)](https://sqlite.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17121f?style=for-the-badge&logo=postgresql&logoColor=e9b452)](https://www.postgresql.org/)
[![JavaScript, no framework](https://img.shields.io/badge/JavaScript%2C%20no%20framework-17121f?style=for-the-badge&logo=javascript&logoColor=b58afb)](docs/viewer.md)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-17121f?style=for-the-badge&logo=githubactions&logoColor=e9b452)](https://github.com/Vanilla-Sphere/sphere-blink/actions)
[![Render](https://img.shields.io/badge/Render-17121f?style=for-the-badge&logo=render&logoColor=b58afb)](docs/deployment.md)

</div>

<details>
<summary><b>Modules</b></summary>

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

</details>

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn sphereblink.main:app --port 8000
# open http://localhost:8000/sky?target=barnard
```

No key and no account are needed: the SPHEREx images and the catalogues are
public. Run it as one web process (`--workers 1`, the default unless
`WEB_CONCURRENCY` is set): views in progress are kept in that process, which
starts its own worker processes for the cutting.

<details>
<summary><b>Settings</b> (environment variables, see <code>sphereblink/config.py</code>)</summary>

| Variable | Default | Meaning |
| --- | --- | --- |
| `SPHEREX_COLLECTIONS` | `spherex_dr1,spherex_qr3` | IRSA collections to search, newest first. A new data release only needs its name added here. |
| `SPHEREX_CACHE_DIR` | `.cache/sphereblink` | Where the SQLite cutout cache lives (put it on a persistent disk). |
| `SPHEREX_FETCH_WORKERS` | `64` | Concurrent exposures being read. |
| `SPHEREX_MAX_FOV_ARCMIN` | `51` | Largest field of view. |
| `SPHEREX_AUTO_MEASURE` | `1` | Measure the five featured targets by itself after start (`0` = only on request). |
| `SPHEREX_WARM_CATALOGS` | `1` | Store the Gaia and CatWISE overlays of the five featured targets after start, so they show at once (`0` = only on request). |
| `DATABASE_URL` | empty | Postgres for measured positions and tracks (`sql/`). Without it they are kept on disk only. |

</details>

The tests (237 of them):

```bash
pip install pytest pyyaml
SPHEREX_AUTO_MEASURE=0 python -m pytest -q
```

## Measurements

`GET /api/measure?target=...` (or `ra=&dec=`) starts a job and is polled until
`status` is `ready`. What it gives on the featured targets (DR1 + QR3,
measured 2026-11-14):

| Target | Positions | Fitted proper motion (mas/yr) | Catalogue | Note |
| --- | --- | --- | --- | --- |
| Barnard's Star | 86 on detectors 5-6 (254 more from saturated wings, not used) | -992 ± 472, +10327 ± 249 | -801.6, +10362.4 (Gaia DR3) | position 0.1 arcsec from the Gaia prediction |
| Epsilon Indi B | 431 | +3945 ± 48, -2513 ± 49 | +3982.0, -2466.8 (Gaia DR3) | 0.6 arcsec from the Gaia prediction (unresolved pair Ba+Bb); brightness constant to 2 % |
| WISE 0855-0714 | 6, of which 5 are stacks of 14-25 exposures | -8766 ± 992, +1536 ± 992 | -8123.7, +673.2 (Kirkpatrick et al. 2021) | invisible in single exposures, found at 4-5 µm in the stacks |
| RW Aurigae | 271 | +316 ± 288, +49 ± 84 | +4.9, -24.6 (Gaia DR3) | no change beyond the 12 % ring accuracy (0.99, 1.14, 1.00 in three passes); most exposures are saturated |
| R Draconis | 515, all from saturated wings | +154 ± 334, +241 ± 426 | -20.6, +6.1 (Gaia DR3) | faded by a factor 2.0 from Jun 2025 to Jun 2026 (Mira variable) |

<details>
<summary><b>What the job does</b> (<code>sphereblink/measure.py</code>)</summary>

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

</details>

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

## Documentation

The product design documentation is in [`docs/`](docs/README.md); every
statement about behaviour was checked against the source code, and every
performance figure states where and when it was measured.

| | |
| --- | --- |
| [Product overview](docs/overview.md) | The problem, the users, the product in one page |
| [Features](docs/features.md) | Every user-facing feature |
| [Science and algorithms](docs/science/README.md) | The physical reason and the algorithm behind every processing step |
| [Architecture](docs/architecture.md) · [Imaging engine](docs/imaging-engine.md) | Components, request flows, range reads, resampling, epochs, progressive fields |
| [Measurement pipeline](docs/measurement-pipeline.md) | Astrometry, track fitting, light curves, bright-star photometry |
| [Moving objects](docs/moving-objects.md) · [How much the search finds](docs/mover-completeness.md) · [Shift-and-stack](docs/shift-and-stack.md) | The blind searches, their validation and their limits |
| [Distance and redshift](docs/distance-redshift.md) | Published distances and redshifts, and the redshift read from the spectrum |
| [API reference](docs/api-reference.md) · [Viewer](docs/viewer.md) · [Code structure](docs/code-structure.md) | Endpoints, the web client, the repository |
| [Data and sources](docs/data-sources.md) · [Performance](docs/performance.md) | Datasets and credits; measured timings |
| [Security and resilience](docs/security.md) · [Deployment](docs/deployment.md) · [Environments](docs/environments.md) | Limits, headers, what happens when a service fails; hosting and release |

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

## Contributing

Problems and ideas are welcome as
[issues](https://github.com/Vanilla-Sphere/sphere-blink/issues). For a change,
branch from `develop` as `feat/...`, `fix/...` or `chore/...` and open a pull
request into `develop`; `main` is production and only takes `develop`
([docs/environments.md](docs/environments.md)). Run the tests first (see
[Run it](#run-it)).

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

<br>

<div align="center">

<a href="https://vanillasphere.com"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/web/logo-mark.svg" alt="" width="56"></a>

**Vanilla Sphere** · second prize, 2026 NASA Space Apps Challenge, Hà Nội

<a href="https://vanillasphere.com"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-live.svg" alt="Open the live site" height="44"></a>
<a href="#what-it-found"><img src="https://cdn.jsdelivr.net/gh/Vanilla-Sphere/sphere-blink@91a93dbd6061497126c979779e99ad3408fc3357/docs/readme/btn-found.svg" alt="See what it found" height="44"></a>

<sub><a href="#top">Back to the top</a></sub>

</div>
