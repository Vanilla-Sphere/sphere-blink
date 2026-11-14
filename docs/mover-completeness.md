# Moving objects: how much the search finds

The blind search ([moving-objects.md](moving-objects.md)) lists what it found.
It cannot say what it missed. This document measures that: the share of the
moving objects in a field that the search finds (its completeness), as a
function of brightness, speed, direction and how crowded the field is, and how
often it reports a path where nothing moves (its false alarms).

- Method: `sphereblink/mover_completeness.py` (no network, no storage).
- Bench: `scripts/mover_completeness.py` (fetches fields, runs rounds, writes the report).
- Numbers the viewer shows: `web/data/mover-completeness.json`, drawn by `web/completeness.js`
  at the end of the Discoveries page.
- Tests: `tests/test_mover_completeness.py`.

RESULTS_PLACEHOLDER

## Method: injection and recovery

Surveys measure their completeness by adding objects they know to their own
data and counting how many their software finds. The same is done here.

1. **Real exposures.** Every exposure of a 50 arcmin field is fetched once, as
   the site fetches it, and kept as one file. Stars, noise, masked pixels,
   cosmic ray hits and detector defects are the real ones. Nothing about the
   field is simulated.
2. **Fake objects.** Twelve per round. Each moves on a straight line at
   constant speed. It starts at a random place of the field at the time of a
   random exposure, in a random direction, at a speed between 2 and 400 arcsec
   per hour (uniform in the logarithm), with a visual magnitude V between 15.5
   and 19.8. A fake exists in one pass of the telescope over the field, as a
   real object does: months later it is elsewhere.
3. **Brightness in every exposure.** Every SPHEREx exposure has another
   wavelength, so a fake needs a spectrum. Two kinds are used, in turn:
   - **an asteroid of the main belt**: the sunlight it reflects (the Sun's
     magnitudes from 0.55 to 5.2 µm of Willmer 2018, ApJS 236, 47) and the
     heat it gives off at 2.7 au from the Sun (the standard thermal model of
     near-Earth asteroids, NEATM, with albedo 0.15, beaming 1.0, emissivity
     0.9). The heat is 7 % of the reflected light at 3.5 µm, 1.5 times it at
     4.3 µm and 9 times it at 5.0 µm. These are the fakes of the figures.
   - **a body without heat of its own**: reflected sunlight only, which is
     what a distant object looks like. Its numbers are given beside the
     others.

   V 18.0 is 0.29 mJy at 1.24 µm for both. At 4.6 µm it is 0.20 mJy for the
   asteroid and 0.045 mJy for the body without heat.
4. **Shape.** A point source through the telescope is a narrow spot that
   falls on 6.15 arcsec detector pixels; the frames of a field are those
   pixels interpolated onto a common grid. A fake goes the same way: a
   Gaussian spot is integrated over detector pixels that lie at a random
   offset and angle to the grid (different in every exposure), and the result
   is interpolated onto the grid with the engine's own rule. While the
   113.6 seconds of an exposure pass the fake moves, and leaves a trail: 0.5
   pixel at 100 arcsec per hour, 2 pixels at 400.
5. **Before anything else.** The fakes are added to the frames before the
   search starts. The static sky of the search is the median of the frames,
   and a slow object partly ends up in it; that loss is part of the result.
6. **The unchanged search.** `movers.search` runs on the frames with the
   fakes, exactly as on the site. Nothing in it knows about the fakes.
7. **Counting.** A fake is found when the search returns a path that passes
   within 20 arcsec of the fake's true place (at the path's own reference
   time) with the fake's motion (within 15 % of its speed, or 3 arcsec per
   hour). Two levels are counted, because the search asks more of a path that
   no catalogue has an object on: **found** is the evidence asked of a known
   object, **strong** the evidence asked of a nameless one (see
   [moving-objects.md](moving-objects.md#steps)). A returned path that is
   neither a fake nor one of the paths the search finds without fakes is a
   false alarm.
8. **Who counts.** Completeness is the share found among the fakes whose
   place could be seen at 8 exposure times or more: inside the field, on
   pixels the exposure has. A fake that crosses a corner of the field in two
   exposures cannot be found at any brightness; such fakes are counted
   separately.

### Is a fake like a real star?

The width of the spot was set so that the brightest pixel of a fake holds the
share of the light it holds for real stars. Measured on isolated bright stars
in single frames of the three fields (237 to 2294 stars per detector and
field) and on 3000 fakes per width:

| | Brightest pixel / all light (median, 16 to 84 %) | 3 × 3 pixels / brightest pixel (median) |
| --- | --- | --- |
| Stars, detectors 1 to 3 | 0.29 to 0.32 (0.24 to 0.40) | 2.8 to 3.1 |
| Fakes, width 0.40 pixel | 0.315 (0.26 to 0.38) | 3.0 |
| Stars, detectors 4 to 6 | 0.24 to 0.27 (0.20 to 0.34) | 3.2 to 3.5 |
| Fakes, width 0.52 pixel | 0.255 (0.22 to 0.30) | 3.6 |

The spread comes from where the star falls on the detector pixels, and the
fakes have it too. On real asteroids the search measured 1.4 to 3.4 for the
second ratio.

### Is a fake like a real asteroid?

The asteroids that the search found in these fields have a magnitude V
predicted by their catalogue. Reading their measured flux as reflected
sunlight gives a V of its own:

| Asteroid | Catalogue V | V from 0.75 to 2.4 µm | V from 3.8 to 5 µm |
| --- | --- | --- | --- |
| (159) Aemilia | 14.0 | 14.3 | - |
| (412) Elisabetha | 13.9 | 14.5 | 12.6 |
| (8895) Nha, two passes | 16.9 | 17.1 and 17.3 | 15.9 and 16.3 |
| (3829) Gunma | 17.3 | 17.8 | 15.8 |
| (15101) 2000 AY150 | 18.4 | 18.3 | 15.6 |

Two things follow. At 0.75 to 2.4 µm a real asteroid is 0.3 magnitude
fainter (median) than a fake of its catalogue magnitude, so the magnitudes of
this document are good to about that. At 3.8 to 5 µm real asteroids are 1 to
3 magnitudes brighter than reflected sunlight: they glow with their own heat,
and the fakes do not. The faintest asteroid found, (15101) at V 18.4, has half
of its sightings there. For the two longest bands the measurement is
therefore cautious: a real asteroid is seen in more exposures than a fake of
the same magnitude.

## What the measurement does not cover

- Objects slower than 2 arcsec per hour: the search does not look for them
  (they are the job of the shift and stack search).
- Objects brighter than V 15.5: their cores are flagged in the Level 2 images,
  which a fake does not reproduce. The asteroids found at V 11.8 to 14.0 show
  that the search handles them.
- Comets: a fake is a point; a coma is not.
- Paths that cross from one field into the next: each field was searched on
  its own.
- Names: whether the catalogues know an object is not part of the search that
  was measured.

## Running it

```
python scripts/mover_completeness.py fetch sparse 167.13 5.17      # 20 to 30 s, 0.5 GB
python scripts/mover_completeness.py prepare sparse                # noise maps and the search without fakes
python scripts/mover_completeness.py run sparse --trials 240       # 12 fakes per round, 35 to 45 s per round and core
python scripts/mover_completeness.py shuffle sparse --trials 100   # searches with the exposure times shuffled
python scripts/mover_completeness.py report sparse medium crowded --plot completeness.png
```

Files go to `.cache/mover-completeness` (`--dir`). A run can be stopped and
started again; rounds are numbered and each has its own seed, so the same
round gives the same fakes. `--search stack` and `--search both` measure the
shift and stack search with the same fakes. This is hours of computing: it
runs on a workstation, never on the server of the site.
