# Faint and slow moving objects: shift-and-stack

The blind search of [moving objects](moving-objects.md) links detections: an
object must stand five times above the noise in single exposures, and move at
least two arcsec per hour. A body far beyond Neptune does neither. It is
faint, and it creeps: Pluto moves 0.3 arcsec per hour, Eris 0.2. This search
adds the method that surveys for distant bodies use. For every motion an
object could have, the exposures are shifted so that such an object stays in
place, and added. Noise averages out, the object does not.

- Algorithm: `sphereblink/mover_stack.py` (no network, no storage).
- Tests: `tests/test_mover_stack.py` (offline, on made-up frames).
- It works on the same frames as the linker (a progressive field of 7 x 7
  cells, 493 pixels of 6.15 arcsec) and returns the same `movers.Track`
  objects, marked `extra["method"] == "stack"`.

## Where the method comes from

| Source | What is taken from it |
|---|---|
| Heinze et al. 2015, AJ 150, 125 (digital tracking) | The grid of motions: spacing = sqrt(2) x largest blur allowed / time span (their eq. 4). A threshold near 7 sigma for a blind search |
| Whidden et al. 2019, AJ 157, 119 and Smotherman et al. 2021, AJ 162, 245 (KBMOD) | The two images per exposure, psi (weighted signal) and phi (weight), and the significance sum(psi) / sqrt(sum(phi)). The sigma-G filter on a path's light curve. Tests on the stacked picture |
| Bernstein & Khushalani 2000, AJ 120, 3323 | A distant body's path described by its distance (gamma = 1/distance) plus a motion of its own that a closed orbit limits |
| Rice & Laughlin 2020, PSJ 1, 81 (TESS) | Shifts by whole pixels on undersampled frames. Known distant objects as the test of a blind search |
| Holman et al. 2004, Nature 430, 865 | Shift-and-stack on a grid of rates and angles as the standard for faint outer bodies |

## What SPHEREx gives the search

Measured on 13 fields (417 to 716 frames each):

- A field is seen in passes of about two weeks, some six months apart. A pass
  holds 20 to 50 visits, about four a day; a visit is a few exposures within
  minutes, each on two detectors at once.
- Every exposure has another wavelength. The frames have no variance plane:
  the noise of a frame is measured in the frame.
- Noise is not Gaussian. Three pixels in a thousand lie beyond 4 sigma (a
  hundred times the Gaussian share), and neighbouring pixels of a resampled
  frame are correlated (0.24). A stack therefore scatters 1.4 to 1.6 times
  more than its frames say. That factor is measured in every window, on the
  stacks themselves, and every significance below is divided by it.
- SPHEREx looks about 90 degrees from the Sun. Earth then moves largely
  along the line of sight and falls towards the Sun across it. Its drift on
  a distant body's path is smaller than at opposition (1 to 93 arcsec per
  hour for a body at 1 au in the passes of the 13 fields, half of them
  below 50, against 148), and the bend it gives the path is at its largest. See
  [Earth's part](#earths-part-distance-from-the-path).

## Steps

1. **A static sky of its own for each epoch.** The median of all frames is the
   static sky of the linker. An object that stays within a pixel for days is
   in a fifth or more of the frames a detector took of that pixel, shows
   faintly in the median, and is masked as if it were a star: Eris, at 3
   sigma per exposure, stands 6 sigma in the median. So the static sky for
   the frames of one epoch is made from the frames of the other epochs only.
   Half a year earlier or later the object is elsewhere.
2. **Frames in units of their noise**, limited to 2.5 sigma. An object worth
   stacking is fainter than that in one frame, and a cosmic ray hit or the
   leftover of a star then cannot carry a stack by itself. Frames with too
   many outliers are left out.
3. **Matched filter.** Each frame is weighted by the brightness expected at
   its wavelength (sunlight reflected by a neutral surface) over its noise,
   and smoothed with the shape of a point source. The frames of a visit are
   added without a shift. This gives psi and phi per visit.
4. **Search.** In every pass each motion on a grid is tried: the visits are
   shifted by whole pixels and added. A first search on pixels of twice the
   size finds seeds; each seed is searched again at full resolution on a
   grid four times finer. Slow motions use the whole pass. Motions between
   1.1 and 4 arcsec per hour use windows of four days, which are less deep.
5. **Three parts.** A path counts only when each third of its time span shows
   the object too. This is tested inside the search.
6. **Vetting in the frames**, see below.
7. **Distance**, from the bend of the path, and the test whether any closed
   orbit allows the motion at that distance.

## Earth's part: distance from the path

Seen from the moving Earth, a body at distance d (in au) is displaced by
Earth's own path divided by d. The straight part of that displacement adds a
drift to the body's motion. The rest bends the path. Both are computed from
Earth's position (astropy's built-in ephemeris, no download) for the times
of the visits and the direction of the field.

- **The bend gives the distance.** In two weeks Earth falls towards the Sun
  by 0.007 au. At 1 au that would bend a path by about 200 pixels; at 35 au
  by 6 pixels, at 95 au by 2. The search tries a few distances. For a path
  that passes the vetting the distance is measured in the frames: the stack
  along the path is computed for a fine series of distances, the path free
  to shift and tilt for each, and the best one is taken. An object that
  single frames show gets its distance from its centroids instead: a line
  plus the bend, by least squares. The range given with a distance is what
  the method proved good for on fake bodies (see
  [validation](#validation-on-real-fields)): at least a factor 2.5 either
  way from the stack, at least 20 % either way from centroids. If the path
  that comes with the distance shows the object clearly less well than the
  path before, the fit went after something else: the path before stays
  and no distance is given.
- **A closed orbit limits the motion.** A body's motion is Earth's drift at
  its distance plus a motion of its own, and that cannot exceed the speed of
  escape from the Sun there (208.8 arcsec per hour at 1 au, falling as
  distance to the power -1.5). Only such motions are searched in the whole
  pass. This cuts the number of motions tried by a factor of four to twenty,
  which makes the search faster and leaves chance fewer tries. A path whose
  measured distance allows no closed orbit is refused (`unbound`).

Checked on NASA's ephemerides (IRSA MOST) before any search: a straight line
misses the true path within one pass by 5 to 68 arcsec; a line plus the bend
fits to 0.05 to 0.4 arcsec, and returns the distance of Pluto, Eris,
Makemake, Haumea, Chiron and Sedna to within 2 %.

## Vetting

Every path the search proposes is measured again in the single frames. It is
kept only if it passes all of these; the names are the keys of
`stats["rejected"]`.

| Key | Test |
|---|---|
| `few_frames`, `few_times` | At least 12 exposure times with a usable measurement |
| `faint` | The stack along the path, against the same stack on 16 parallel paths 7 and 11 pixels away. The parallel paths give the local scale; it is never taken smaller than the factor measured in the window |
| `no_motion` | The path covers at least 18 arcsec (three pixels) |
| `not_compact` | The stacked picture is a point source at the predicted place: brightest pixel within 1.5 pixels, light in the eight neighbours, no halo three pixels out |
| `static` | Each frame is compared with the same pixel of the sky in frames taken while the object is elsewhere on its path. A leftover of a star is there with or without the object and cancels |
| `near_star` | At least 30 % of the weight lies in frames more than three pixels from a bright star, and those frames by themselves show the object: half of what their share should give, or 3 sigma. What a bright star leaves at the edge of its mask changes with wavelength and from visit to visit, and every false path above 7 sigma in the first runs was made of it |
| `few_visits` | At least six visits, three of which show the object by themselves |
| `uneven`, `one_part` | Most visits are at least positive; each third of the frames shows the object. Waived for an object that single frames show at 5 sigma in three visits: it has shown that it moves, and it need not be bright at every wavelength |
| `at_rest` | A stack without motion, the mask ignored, at the start, middle and end of the path must stay below three quarters of the moving stack. A variable star with a masked core leaves a ring that can pass for something crawling along it |
| `unbound` | No closed orbit around the Sun allows the motion at the measured distance |
| `known` | The linker has this object already |

Frames in which a brighter moving object lies within four pixels are not
used, so no path is built from another object's light.

One frame counts for at most 3 sigma. The limit is lifted only for an object
that single frames show at 5 sigma in three visits or more, each at another
place (two pixels apart or more), over a day and a half: then a frame counts
up to one and a half times the median of the visits of its detector. A
cosmic ray hit cannot lift its own limit, and what a star leaves at one
pixel in two visits cannot either.

## Two levels of evidence

- A path of **5 sigma** or more is returned. At this level chance still
  produces a path in every second field, so such a path counts only when a
  catalogue has an object at that place with that motion. This is how Orcus
  and Eris are found.
- A path of **7.5 sigma** or more has `extra["stack_strong"] == True`. Only
  these may be shown without a catalogue name, and then as a candidate.

`chance_tracks` measures what chance gives in a field: the same search with
the times of the visits (or of the single exposures) shuffled within each
pass. The pictures stay, their order is lost, and with it every real motion.
The moving objects that the true times show (the linker's and the stack's
own) are passed as `real`, so that their light is kept out, as it is in the
real search through `exclude`. A bright asteroid still leaves light in each
frame after the shuffle, and that is no chance.

## Validation on real fields

All numbers below are from version 1 of the module as it is in the
repository. The real fields were run as production runs them: the linker
first, then this search with the linker's tracks as `exclude`.

### Known distant bodies, found blind

Six fields were chosen because NASA's ephemerides put a known body there. The
search was told nothing about it. The linker found none of the six: all move
slower than two arcsec per hour, and all but Pluto are too faint in single
exposures.

| Body | V | Significance | Median offset from NASA's ephemeris | Distance measured (range) | True distance |
|---|---|---|---|---|---|
| Pluto | 14.5 | 279 sigma | 0.2 arcsec | 34.6 au (28.8 to 41.5), centroids | 35.2 au |
| Makemake | 17.1 | 17.0 sigma | 1.3 arcsec | 62 au (25 to 155) | 52.5 au |
| Haumea | 17.3 | 9.6 sigma | 3.6 arcsec | 35 au (14 to 86) | 50 au |
| Chiron | 18.5 | 8.5 sigma | 3.6 arcsec | 16 au (6.5 to 41) | 18.3 au |
| Orcus | 19.1 | 7.4 sigma | 3.9 arcsec | 36 au (15 to 91) | 48 au |
| Eris | 18.7 | 5.6 sigma | 4.0 arcsec | 93 au (37 to 249) | 95.5 au |

A pixel is 6.15 arcsec, so every one of them is placed to better than a pixel.
Pluto, Makemake, Haumea and Chiron stand above 7.5 sigma and would be shown
without a name. Orcus and Eris are at the 5 sigma level: they count because
the catalogue has a body at that place with that motion.

Not found: Gonggong (V 21.5) and Varuna (V 20.5, in its field for nine days
only) are below what the frames hold. Ixion (V 19.4) lies towards the
Galactic bulge: 463 of the field's 703 frames are left out as too crowded,
and half of the sky is masked or next to a bright star.

### What chance gives

- **Thirteen real fields** (the six above, the fields of Varuna, Gonggong
  and Ixion, Barnard's star, three fields without a target): no track
  without a name at 7.5 sigma or more. Six tracks without a name between 5.0
  and 6.1 sigma, about one in every two fields: this is why that level needs
  a name.
- **Shuffled times.** 96 searches on two fields (Barnard's star, and a
  field in which the linker finds eight asteroids), 24 with the times of
  the visits shuffled and 24 with the times of the exposures shuffled on
  each, the linker's objects kept out, no limit on the time so that no
  window is skipped: no track at 7.5 sigma or more. The strongest is 5.9
  sigma. 25 tracks between 5.1 and 5.9 sigma came out of 19 of the
  searches: two in the 48 of Barnard's field, 23 in the 48 of the other.

### Fake bodies on real orbits

100 fake bodies were added to the real frames of two fields (Orcus and Eris,
50 each): distance 30 to 110 au, own motion inside what a closed orbit
allows, Earth's part and the bend included, V 16.5 to 20.5 with the colour
of sunlight. Their motions came out between 0.17 and 3.0 arcsec per hour.
The search ran by itself, without the linker before it. A track counts as
the fake when it lies within 20 arcsec of it and moves as it does, at the
track's own reference time.

| V | Fakes | Found | Of them at 7.5 sigma |
|---|---|---|---|
| 16.5 to 18.0 | 33 | 31 | 31 |
| 18.0 to 18.5 | 15 | 14 | 14 |
| 18.5 to 19.0 | 24 | 22 | 22 |
| 19.0 to 19.5 | 6 | 4 | 2 |
| 19.5 to 20.0 | 15 | 6 | 2 |
| 20.0 to 20.5 | 7 | 1 | 1 |

- **78 of 100 found**, 72 of them at 7.5 sigma. Brighter than V 19: 67 of 72.
- **By what one exposure shows** (median over the fake's frames): below
  1 sigma 3 of 15, 1 to 2 sigma 10 of 16, 2 to 3 sigma 20 of 21, above
  3 sigma 45 of 48. An object at 2 sigma per exposure is found; the linker
  needs 5.
- **The linker** on the same frames of the Orcus field: 7 of 50 (all V 18.1
  or brighter, five of them faster than 2 arcsec per hour). This search: 39
  of 50.
- **What is lost.** All five fakes brighter than V 19 that were missed lay
  next to a bright star for most of their path (`near_star`). In the Orcus
  field three faint fakes were refused as `at_rest`: a leftover of a star
  lay on their path. These two rules are what keeps false paths out, and
  this is their price.
- **Distance.** From centroids (37 fakes, the brighter ones): half within
  3 %, 36 within 16 %, one off by a factor 1.6. From the stack (21 fakes):
  half within 21 %, 12 within 30 %, the worst off by a factor 3; every one
  inside the range given. 20 found fakes got no distance: 11 were found in
  the four-day windows, which are too short for the bend to show.

### Time

One to three minutes for a field (417 to 716 frames), on one core: the
search does not use more. Orcus's field took 51 s of wall time and 51 s of
processor time. Past the budget (`budget_s`, 300 s by default) the four-day
windows are the first to be skipped (`stats["skipped_windows"]`); the
whole-pass search, which finds the distant bodies, always runs.

## Limits

- **How faint.** Half of the fakes are found at V 19.5, nine in ten
  brighter than V 19; the gain over single exposures is about 1.5
  magnitudes. That holds for a body with the colour of sunlight that all
  frames of a pass cover. Eris (V 18.7) comes out at 5.6 sigma only. Its
  surface is methane ice, which absorbs over much of the range, as on
  Makemake, and at 1 micron the stack measures it at AB magnitude 19.3. Sedna (20.8) and Gonggong (21.5) are
  out of reach.
- **How far.** A path must cover 18 arcsec within a pass. Beyond about 250 au
  a body's own motion and the bend together stay below that. A planet at 500
  au does not move measurably within two weeks. It would stand still in each
  pass and sit 13 arcmin away half a year later, because of Earth's
  parallax. Finding that needs a search that pairs faint sources between
  passes, which this module does not do.
- **Bright stars and crowded fields.** What is masked as a star, or lies
  within three pixels of a bright one, is not trusted: a third of the sky
  in an ordinary field, half of it in a crowded one
  (`stats["clear_share"]`). Frames with too many outliers are left out, which in a
  field towards the Galactic bulge is most of them (Ixion's field: 463 of
  703).
- **Colour.** The weights assume reflected sunlight. An object with strong
  ice bands (Makemake beyond 1.5 micron) is found through the frames in which
  it is bright.
- **Motions between 1.1 and 4 arcsec per hour** are searched in windows of
  four days only and reach less deep. Above 4 arcsec per hour the linker
  takes over.
- **Shuffled times are not a proof.** They measure what noise and leftovers
  give. A real object that is bright enough still leaves light along its
  path after the shuffle, which is why objects found with the true times are
  left out of the shuffled searches.

## Interface

```python
out = mover_stack.search(cube, frames, wcs, scale_arcsec, sky=None, exclude=None,
                         kind="sun", budget_s=300.0, max_tracks=20, avoid=None)
out["tracks"]   # movers.Track, strongest first
out["stats"]    # windows, rejected (by key), candidates, passes, epochs, clear_share, seconds
```

`frames` are the linker's: `exposure`, `mjd`, `detector`, `wave_um`. A frame
that does not cover the centre of the field has no wavelength (None or
NaN): it is weighted by the middle of its detector's range.
`sky` is the static sky of `movers.static_sky` when it exists already.
`exclude` are the linker's tracks: they are not reported again and their
light is kept out. `budget_s` bounds the time: the whole-pass search always
runs, the four-day windows only while time is left
(`stats["skipped_windows"]`).

`Track.extra` of a stacked path:

| Key | Meaning |
|---|---|
| `method` | `"stack"` |
| `stack_snr` | Significance in the frames, on the local scale |
| `stack_strong` | 7.5 sigma or more: may be shown without a name |
| `stack_distance` | `distance_au`, `distance_min_au`, `distance_max_au`, `from` (`centroids` or `frames`) |
| `stack_mag_ab`, `stack_flux_mjy` | Brightness at 1 micron |
| `stack_visits`, `visits_seen`, `stack_visits_positive`, `stack_sighted_visits` | Visits judged, visits that show the object by themselves, visits above zero, visits with a 5 sigma frame |
| `stack_parts` | Significance of each third of the frames |
| `stack_stamp` | The test of the stacked picture |
| `stack_tier` | `slow` (whole pass) or `mid` (four days) |
| `points` | One entry per frame, as the linker's confirmation writes them. `seen` is weak here by nature: most frames show nothing |
| `times_seen`, `times_covered`, `covered`, `seen` | As for the linker. For a stacked path use `stack_strong`, not `movers.strong` |

## Reproduce

```python
from sphereblink import movers, mover_stack
sky = movers.static_sky(cube, [f["detector"] for f in frames])
real = mover_stack.search(cube, frames, grid.wcs, grid.scale_arcsec, sky=sky)
chance = mover_stack.chance_tracks(cube, frames, grid.wcs, grid.scale_arcsec, sky=sky,
                                   trials=24, real=real["tracks"])
```

Ephemerides for the comparison come from IRSA MOST
(`https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most`, catalog `spherex`,
object by NAIF number).
