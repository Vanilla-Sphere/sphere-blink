# Moving objects: the blind search

The measurement pipeline ([measurement-pipeline.md](measurement-pipeline.md))
measures an object whose position is given. The blind search answers the
opposite question: with nothing known beforehand, what moves in a field? It
finds asteroids, comets and anything else that crosses a field between
exposures, keeps what it finds in a log on the server, names the objects that
are already known, and lets visitors say whether a path looks real.

- Algorithm: `sphereblink/movers.py` (no network, no storage).
- Store: `sphereblink/mover_store.py` (local SQLite, `movers.sqlite` in the cache directory).
- Service and endpoints: `sphereblink/movers_api.py`.
- Names of known objects: `sphereblink/known_objects.py`.
- In the viewer: `web/movers.js` ([features](features.md#17-blind-search-for-moving-objects)).

## What it works on

A progressive field ([imaging engine](imaging-engine.md#progressive-fields)):
7 x 7 cells of 0.12 degrees, 50.4 arcmin across, every exposure resampled on
one north-up grid of 6.15 arcsec pixels. A field holds several hundred
exposures (450 to 713 in the three test fields) taken in bursts: about four
exposures a few minutes apart on each orbit, the orbits 96 minutes apart, for
a few days per pass; passes are about six months apart. Every exposure has
another wavelength, and the two detectors behind one dichroic are exposed at
the same time.

The search needs no extra archive reads. It runs on the frames that the view
already put in the disk cache.

## Steps

1. **Static sky.** The median of the frames of one detector, pixel by pixel,
   holds everything that stays in place. A moving object is in a given pixel
   for a few exposures only and drops out of the median. Pixels where the
   median is bright (stars, galaxies) are masked.
2. **Detections.** In every frame minus its static sky: groups of 2 to 400
   connected pixels with a peak of 5 sigma or more above the local noise
   (measured in tiles of 32 pixels). At most 80 per frame. Detections that
   come back at the same place of the sky at times too far apart for a moving
   object are dropped: they are leftovers of stars and detector defects.
3. **Linking.** Per pass of the telescope (a gap of more than 2 days starts a
   new pass). Every pair of detections proposes a motion between 2 and 600
   arcsec per hour; all detections of the pass vote on it. A proposal is kept
   when detections at 4 or more different exposure times, in 2 or more visits,
   lie within 7.5 arcsec of the straight line (growing by 0.15 arcsec per
   hour away from the pair that proposed it, to at most 15 arcsec). The best supported line is taken first and
   its detections are removed. Pieces that continue each other on one line
   are merged. When a pass holds more than 3000 detections, its 3000
   brightest take part.
4. **Confirmation in the pixels.** Along a track, every exposure that covers
   the predicted place is measured there, in the detectors the object was
   detected in. The object counts as seen in an exposure when two things
   hold: the brightest pixel within two pixels of the predicted place is 3.5
   sigma above the noise, and that pixel with its eight neighbours is a
   compact source (together 3 sigma above their noise, and holding at least
   1.3 times what the one pixel holds). The telescope spreads the light of a
   real object over neighbouring pixels (1.4 to 3.4 times the brightest pixel,
   measured on the asteroids below); a noise peak, a cosmic ray hit or the
   leftover of a star is one pixel. A track is kept when the object is seen
   at 6 or more exposure times, at 70 % or more of the times its path was
   covered, and in half or more of the covering exposures. A detection that
   proposed the track counts only where the pixels at the predicted place
   pass the same test. Counting exposure times and not exposures matters: two
   detectors exposed together are one piece of evidence. This is the step
   that removes chance alignments.
5. **Curved paths.** Over a few hours an object moves on a straight line.
   Over days its motion changes, mostly because the telescope goes round the
   Sun with the Earth, and a straight line misses it by tens of arcsec. A
   path of 20 hours or more, seen at 6 or more times in 3 or more visits, is
   fitted with a constant acceleration as well; the curve is taken when it
   leaves less than 70 % of the line's scatter, at least 1 arcsec less, and
   needs no more than 0.2 arcsec per hour squared (measured: 0.03 for Astraea,
   0.04 for Nha). Two confirmed pieces up to 48 hours apart are one object
   when one such curve holds both within 7.5 arcsec rms and every detection
   within 15 arcsec. A detection further off, of which a straight piece
   takes one up now and then, is left out of the common path instead of
   keeping the pieces apart, as long as there are no more of them than 3 %
   of the detections (one at least). Pieces are joined
   only after the pixels have confirmed each of them: the long path of a
   faint object crosses visits in which it is too faint to be seen and would
   fail step 4 as a whole (Nha: 20 of 29 times, although it lies within 5
   arcsec of JPL's position everywhere). A piece the pixels did not confirm
   on its own is added to a confirmed path when its detections lie on it.
6. **Chance figures.** The same search is repeated with the exposure times
   shuffled within each pass: the pictures stay, their order is lost, and
   with it every real motion. What is found then is found by chance
   (`stats.chance_tracks`, per search). A second test shuffles whole visits
   and counts only paths with the evidence step 8 asks for
   (`stats.chance_candidates`); it is run without the detections of the
   paths already seen in three visits or more, because an object that really
   moves keeps part of its path when visits change places. See
   [the static pair](#the-static-pair-why-a-path-without-a-name-needs-three-visits)
   for why the first test is not enough.
7. **Names.** Each track is compared with the known asteroids and comets
   predicted for its place and time (see [below](#names-of-known-objects)).
8. **More is asked of a path without a name.** Nearly everything bright
   enough for SPHEREx is a known asteroid or comet, so a path that no
   catalogue has an object on is far more often chance than a new object. It
   is kept only when seen at 8 or more exposure times, at 80 % or more of
   the times covered, and either in 3 or more visits at 2 or more exposure
   times in each (`visits_seen`) or in 4 or more visits however often in
   each (`seen_by_visit` lists the sightings per visit).
   `stats.unnamed_dropped` counts the rest. A path
   joined from pieces passes when one of its pieces does. In the viewer such
   a path is called a candidate, never a discovery, and is listed after the
   objects the catalogues confirm.
9. **When no catalogue answers.** A path cannot be called known or unknown
   then. With the evidence of step 8 it is shown as "not yet compared with
   the catalogues". With less it is stored but not shown, listed or served
   (`stats.waiting`). The services are asked again after the next search, at
   most every 10 minutes while the log is being read, and after every start
   of the server: the path then gets its name, becomes a candidate, or is
   taken out without ever having been shown.

A track is its reference time `mjd0`, position `ra0`, `dec0`, the motion at
that time `v_east`, `v_north` in arcsec per hour, and for a curved path the
change of the motion `a_east`, `a_north` in arcsec per hour squared (0 for a
straight one): `east = v_east * t + a_east * t^2 / 2` with `t` in hours from
`mjd0`. Speed classes: `slow` below 20, `fast` from 20, `very fast` from 60
arcsec per hour.

## What it was checked against

Three fields along the path of comet 3I/ATLAS, searched with the final
thresholds on 2026-11-14 (local run, frames from IRSA):

| Field | Exposures | Tracks kept | Tracks by chance (8 shuffled searches) |
| --- | --- | --- | --- |
| RA 167.13, Dec +5.17 (December 2025) | 535 | 1 | 0.0 per search |
| RA 248.37, Dec -17.00 (August 2025 to August 2026) | 713 | 9 | 0.0 per search |
| RA 101.65, Dec +20.42 (September 2025 to September 2026) | 450 | 4 | 0.0 per search |

Every one of the 14 tracks is a known object. Each was compared with SkyBoT's
predictions for its time (position within 8 arcsec and motion within 0.3
arcsec per hour for the asteroids):

| Track | Object | Motion found (arcsec/h) | Visual magnitude |
| --- | --- | --- | --- |
| Field 1 | 3I/ATLAS (interstellar comet) | 174.9 | - |
| Field 2 | 3I/ATLAS | 99.4 | - |
| Field 2, two passes | (5) Astraea | 15.7 and 17.9 | 11.8 |
| Field 2, two passes | (8895) Nha | 40.3 and 42.3 | 16.9 |
| Field 2 | (8838) 1989 UW2 | 28.7 | 17.3 |
| Field 2 | (3829) Gunma | 31.7 | 17.3 |
| Field 2 | (412) Elisabetha | 18.9 | 13.9 |
| Field 2 | (159) Aemilia | 10.8 | 14.0 |
| Field 3 | (3780) Maury | 31.4 | 17.9 |
| Field 3 | (1673) van Houten | 40.9 | 17.3 |
| Field 3 | (2448) Sholokhov | 30.2 | 16.1 |
| Field 3 | (1494) Savo | 57.3 | 16.4 |

For 3I/ATLAS the positions were also compared exposure by exposure with IRSA's
Moving Object Search Tool: 174.9 against 168 arcsec per hour in field 1, 99.4
against 99 in field 2. SkyBoT's own orbit of 3I/ATLAS is 46 to 90 arcsec
behind along the path, which is why a fast object may lie up to 300 arcsec
from its prediction and still match when its motion agrees.

### The audit of 2026-11-14 (why the rules of step 4 and 8 exist)

The first version of step 4 counted an exposure as a sighting from the
brightest pixel alone. On the production site that held for the fields above,
but the field of Barnard's star (RA 269.46, Dec +4.74, galactic latitude 14
degrees, 518 exposures, half of them with more leftovers of stars than the
80 detections a frame may have) gave three tracks, and two were false:

| Track | Verdict | Evidence |
| --- | --- | --- |
| 48.2 arcsec/h, 8 detections in D2 and D5 | (15101) 2000 AY150, real | all 8 detections within 4.8 arcsec (median 1.9) of JPL's position for their exposure, same speed, V 18.4; compact source at 10 of 11 times |
| 186 arcsec/h, 4 detections in D1 | chance | no catalogue object; brightness at the predicted places scatters around zero (-0.12 to 0.15 mJy); no compact source in any of 7 covering exposures |
| 34 arcsec/h, 4 detections in D1 and D5 | chance | no catalogue object; compact source in 3 of 9 covering exposures |

All nine named objects in the log at that time (3I/ATLAS, Astraea twice, Nha
twice, Elisabetha, Gunma, Aemilia and 2000 AY150) were compared, detection by
detection, with JPL's ephemeris through IRSA's Moving Object Search Tool: 129
of 129 detections lie in exposures JPL says cover the object, at a median of
1.4 to 3.4 arcsec from the predicted position (largest 9.9 arcsec, on the
saturated Astraea), with the speeds agreeing to 0.1 arcsec per hour (0.7 for
Elisabetha).

With the compact source test the Barnard field keeps the one real asteroid,
field 2 keeps its nine objects, and 32 shuffled searches (24 of the Barnard
field, 8 of field 2) find no track; before, one of 6 shuffled searches of the
Barnard field found one. Of 542 chance paths linked in those shuffled
searches of the Barnard field none was seen at 8 exposure times, the level
step 8 asks of a path without a name.

### The static pair (why a path without a name needs three visits)

Searching eight more fields the same evening, away from the ecliptic and in
the plane of the Milky Way, showed a kind of false path that the tests above
cannot see. In the field RA 281.22, Dec -2.58 (653 exposures, 502 of them
with more leftovers of stars than a frame may have) eleven paths passed
steps 3 and 4, and none of them is an object:

- A leftover of a star stays at one place of the sky through the 2 to 4
  exposures of one visit. A second leftover does the same in another visit.
  A line through the two places is a path with 4 to 6 detections in two
  visits, and every exposure that covers it "sees" a compact source: 4 of 4
  to 8 of 8 exposure times. Step 4 measures the same two bursts again and
  cannot refute it; no exposure of that detector lies between them.
- Shuffling the exposure times tears each pair apart, so the chance figure
  of step 6 was 0 for this field while the sky itself made eleven.
- The completeness test with injected sources found the same independently in the field RA 167.13, Dec +5.17: two
  injected sources that hardly move, 11 hours apart, came out as one path of
  75.6 arcsec per hour seen at 8 of 8 times.
  What the rule of three visits costs and what it keeps out is measured in
  [how much the search finds](mover-completeness.md#what-the-numbers-say).

All eleven are seen in exactly two visits. Hence step 8: a path that no
catalogue has an object on must be seen in three visits, at two or more
exposure times in each. Three leftovers on one line at the right times is
what the visit shuffle of step 6 counts. Twelve fields with version 3, six
shuffles of each kind per field:

| Field | Exposures (with too many leftovers) | Paths after step 4 | Shown | Chance paths, exposures shuffled | Chance candidates, visits shuffled |
| --- | --- | --- | --- | --- | --- |
| Barnard's star, 269.46 +4.74 | 518 (267) | 1 | 1 named | 1 in 6 | 0 |
| 3I/ATLAS, 167.13 +5.17 | 535 (6) | 1 | 1 named | 0 | 0 |
| 3I/ATLAS, 248.37 -17.00 | 713 (131) | 7 | 7 named | 0 | 0 |
| 3I/ATLAS, 101.65 +20.42 | 450 (63) | 4 | 4 named | 0 | 0 |
| Epsilon Indi, 331.14 -56.82 | 737 (188) | 0 | 0 | 0 | 0 |
| WISE 0855-0714, 133.74 -7.26 | 495 (23) | 0 | 0 | 0 | 0 |
| RW Aurigae, 76.98 +30.42 | 695 (144) | 2 | 2 (9 and 4 visits) | 1 in 6 | 0 |
| R Draconis, 248.22 +66.78 | 1081 (109) | 0 | 0 | 1 in 6 | 0 |
| Galactic plane, 281.22 -2.58 | 653 (502) | 11 | 0 | 0 | 0 |
| Galactic bulge, 270.06 -25.02 | 383 (312) | 1 | 0 | 0 | 0 |
| Galactic latitude 5, 285.06 +4.98 | 596 (400) | 0 | 0 | 1 in 6 | 0 |
| Ecliptic, 190.02 -4.02 | 578 (5) | 1 | 1 (3 visits, 12 of 12 times) | 0 | 0 |

- The 12 named objects are the 14 tracks of the first table: Astraea and
  Nha are one object each now. The three shown without a name in this table
  were searched where no catalogue service can be reached; on the server
  they are compared like any other.
- The four chance paths of the exposure shuffle (and one more in 24 further
  shuffles of the Barnard field) are seen at 6 to 7 exposure times in two
  visits: enough for step 4, not for step 8. A path like that is shown only
  under the name of a catalogue object that is at its place with its motion.
- 96 visit shuffles (72 above, 24 more of the Barnard field) gave no path
  with the evidence of step 8.
- What the rule costs: a real object that no catalogue lists and that is
  seen in only two visits is not shown. (412) Elisabetha and (159) Aemilia
  have two visits in field 2 and are shown because the catalogues name them.

The threshold of a sighting (step 4) was measured again on these fields: at
30,000 random empty places per field an exposure "sees" a compact source in
2.0 to 3.4 % of the cases (5 to 12 % with the brightest pixel alone), and at
JPL's positions of the real asteroids in 70 to 76 %. No other choice of the
three numbers separated the two better, so they stayed.

### What the injection test changed (version 4)

The completeness test ([how much the search finds](mover-completeness.md))
puts artificial asteroids into real frames and counts how many come back.
Within two hours of version 3 it showed two things, both fixed the same
evening:

- **Three visits of two was too much to ask where the telescope gives one
  exposure per visit.** In the sparse field an artificial object was
  typically seen as [2, 1, 1, 1, 1, 2, 1, 1, 1]: nine visits, eleven of
  eleven times, and not "strong". Counting four visits of any size as well
  brings the share of found bright objects that are strong from 81, 88 and
  82 % (sparse, medium, Barnard field) to 85, 89 and 88 %. No chance path
  passes: none of 288 shuffled searches of twelve fields was seen in four
  visits at 8 times and 80 %, and none of the 14 false paths of the
  injection rounds. Any three visits would have let one of those through.
- **The linking looked at too few detections.** A pass holds 3000 to 6000
  detections in a field with many stars, and the linking took the 1500
  brightest, so the cut sat at 7 to 8 sigma, not 5. (3780) Maury kept 11 of
  its 18 detections, (8838) 1989 UW2 5 of 14. With 3000 (and the inner step
  of the linking in single precision, 2.5 times faster, same tracks) Maury
  is linked with 17 and 1989 UW2 with 10, (412) Elisabetha gains a visit and
  passes step 8 on its own, and on the Barnard field 3 more of 44 bright
  artificial objects are found and none is lost.

Version 4 on the twelve fields of the table above: the same objects, and
144 shuffled searches (6 of each kind per field) without a path of the
evidence of step 8.

What was tried and not taken: using frames with more than 80 detections
(up to 250, or their brightest 80 or 250). Barnard's field loses half its
frames to that limit and stays clean with any of the three, but the plane of
the Milky Way, the bulge and galactic latitude 5 then give about one chance
path with the evidence of step 8 per six visit shuffles, and the fields rich
in asteroids lose real ones (Elisabetha, 1989 UW2, Maury), because the extra
detections push the faint real ones out of the 3000 that are linked.

### One stray detection kept Astraea in two (version 5)

Version 4 went to production at 22:48 on 2026-11-14 and brought back the
same seven named objects, but (5) Astraea with 39 detections where version
3 had 77. Replayed on the frames of the server: the two pieces of its path
(39 detections each, 47 and 104 hours) fitted one curve with 4.7 arcsec rms,
and one of the 78 detections lay 15.8 arcsec from it, 0.8 more than a
detection may. With twice as many detections in the linking, one piece had
taken up a detection beside the path. The pieces stayed apart, and the
store then lost one of them: an id is made from a path's first detection,
the first piece was stored under the id the object had before, and that was
the id the second piece made for itself.

- A stray detection is now left out of the common path (step 5). On the
  frames of the server Astraea is one path again: 77 detections, seen at 52
  of 52 times.
- Two paths of one search never get the same id
  (`mover_store._new_id(track, taken)`).

The wider frame limit was measured again the same night, now only where it
could help: 250 detections per frame and 5000 per pass on the nine fields
outside the Milky Way. Barnard's field found 33 of 44 bright artificial
objects where the limit of 80 found 22 in the same rounds (the completeness
test of that night), and nothing real was
lost. But 144 shuffled searches gave one chance path with the evidence of
step 8 (Barnard's field, 5 detections, seen at 12 of 13 times), the
injection rounds one more (4 detections in 3 visits), and the linking and
the look at the pixels took 22 to 32 seconds per search in the fields with
many stars where they take 3 to 8, which makes about three minutes per
field with its four shuffled searches instead of one. Asking a path without
a name for detections at 6 or more times would have stopped both, a rule
made to fit two cases. It was not taken.

Limits found in the same test:

- 3I/ATLAS at 8.7 arcsec per hour and V about 15.2 (field 3, March 2026) was
  not found: faint and slow, it moves less than a pixel between the exposures
  of one visit.
- An object seen in two passes days apart was stored as two tracks (Astraea
  and Nha above): a straight line does not describe several days of motion.
  Version 3 joins them (step 5).
- The cores of bright moving objects are flagged in the Level 2 images, so the
  detections of a bright comet are its halo, and their positions scatter by a
  few arcsec (rms 7.7 arcsec for 3I/ATLAS in field 1).
- A search takes 30 to 55 seconds and about 1 GB of memory per field.

## The second search: faint and slow objects

The steps above need an object to show in single exposures and to move two
arcsec per hour or more. What is fainter or slower (a dwarf planet, a
centaur, anything beyond Saturn) is searched for by adding the frames up
along every possible path: `mover_stack`, described with its validation in
[shift-and-stack.md](shift-and-stack.md). It found Pluto, Makemake, Haumea,
Chiron, Orcus and Eris blind, none of which the linker finds.

It is wired in as a second stage of a field's search (`movers_api.run_stack`),
not as a part of the first:

- **The linker's results come first.** They are stored and shown as before,
  about a minute after the field has loaded. The second search then takes
  one to three minutes more (185 s for the 713 frames of field 248.34 -16.98,
  where it finds nothing and reports nothing), and what it finds is added to
  the field (`mover_store.add_tracks`). Meanwhile the field is `ready` and
  `job.stack` says `searching`; the viewer asks again every 15 seconds and
  says that it is still looking.
- **It has a version of its own** (`mover_stack.VERSION`), noted in the
  field's record (`stats.stack.version`). Putting it in, or changing it,
  does not hide the log: `movers.VERSION` stays what it is. After a start
  the server first searches the fields of an earlier linker version again
  (linker only), then goes through the fields the second search has not seen
  (`movers_api.stack_missing`), one at a time, from frames still in the
  cache.
- **The linker's paths are kept out** (`exclude`): the paths stored for the
  field are not reported again and their light is not taken for something
  new.
- **What is shown follows the same rule as before, with its own level.** A
  path with a catalogue name is shown. Without a name it is shown as a
  candidate only from 7.5 sigma on (`stack_strong`; `movers.strong` answers
  with it for a stacked path). Between 5 and 7.5 sigma chance gives a path in
  every second field, so such a path is dropped when the catalogues have no
  object on it, and stored out of sight while no catalogue has answered.
  Orcus (7.4 sigma) and Eris (5.6) are in the log through their names.
- **A stacked path is not pieced together from two fields.** When an
  overlapping field finds the same object, the stronger find stays whole;
  where the linker has the object, the linker's record stays.
- **When it fails, the field keeps what the linker found** and the second
  search is tried again after the next start.
- `SPHEREX_MOVER_STACK=0` turns the second search off;
  `SPHEREX_MOVER_STACK_BUDGET_S` (180) is the time after which its four-day
  windows are skipped (the whole-pass search always runs).

The names could not be checked from the development machine (SkyBoT and JPL
cannot be reached from there), so they were checked on production after the
release (2026-11-15). The field of Pluto (RA 304.14, Dec -23.58, 472
exposures): the linker found nothing, the second search found three paths in
169 seconds; SkyBoT named the strongest "134340 Pluto" at 0.2 arcsec (279.6
sigma, 0.22 arcsec per hour), and the two others, nameless and below 7.5
sigma, were dropped. The four fields stored before the release were searched
by the server itself in 30 to 243 seconds each and gained nothing; the two
largest ran into the time budget and skipped three windows.

The list names a known object by the class of its orbit as SkyBoT gives it:
"known trans-Neptunian object" for Pluto (class `KBO>Resonant>3:2`), "known
centaur", "known asteroid" for the classes of asteroids, "known comet"; "known
minor planet" when the name comes from JPL, which gives no class, and "known
body" for a class the viewer does not know. Before this every named object
that was not a comet was called "known asteroid".

## Growing with the map

The search is tied to what visitors open. When a progressive field has all its
frames, `tiles.Fields` tells `movers_api` (`tiles.listeners`), and the field is
searched in a process of its own, one field at a time, at low priority. Asking
for a field's objects (`/api/movers/field`) starts the search too when the
field is loaded and has not been searched.

- Detections are stored by sky cell. When a neighbouring or overlapping field
  is searched later, the stored detections within 0.6 degrees join the
  linking, so a path that crosses the border of a field is found once both
  sides have been opened.
- An object found again (the same path: motions within 10 % or 3 arcsec per
  hour, each line passing within 25 arcsec of where the other was measured,
  no more than 24 hours apart) keeps its id, its date of discovery and its
  votes; the detections of both finds are joined.
- A field is searched again only when it has gained exposures or the
  algorithm version (`movers.VERSION`) has changed.
- The log shows only what the current version of the search has found.
  Objects stored by an earlier version stay in the database but are not
  listed or served until their field is searched again; an object found again
  keeps its id, its date of discovery and its votes, with the new evidence
  in place of the old. Version 2 (2026-11-14) is the compact source test,
  version 3 (same day) curved paths and three visits for a path without a
  name, version 4 (same day) four visits of any size and 3000 detections
  per pass, version 5 (same day) a stray detection no longer keeps a curved
  path in two.
- 90 seconds after a start of the server, the fields an earlier version went
  through are searched again by themselves, one at a time, newest first,
  when 95 % or more of their frames are still in the disk cache
  (`movers_api.refresh_stale`). The others wait until somebody opens them.
- Before a search starts it waits, up to 10 minutes, for 1800 MB of memory to
  be free in the container (`SPHEREX_MOVER_MIN_FREE_MB`); if there is none it
  gives up, and the field is searched the next time it is asked for.
- `SPHEREX_MOVER_SEARCH=0` turns the search off; the log stays readable.

Fields smaller than 20 arcmin, or with fewer than 40 frames in the cache, are
not searched: they hold too little of a path.

## Names of known objects

For each pass in which tracks were found, one request asks for the solar
system objects in the field at that time; the rates in the answer carry each
object to the time of each track. A track gets the name of an object when
their motions agree (difference below 25 % of the speed, or 4 arcsec per hour)
and the object lies within 30 arcsec of the track (300 arcsec for objects
faster than 60 arcsec per hour, whose predictions are less certain and whose
parallax from low Earth orbit is larger).

| Order | Service | Notes |
| --- | --- | --- |
| 1 | SkyBoT cone search (IMCCE, Observatoire de Paris) | Answers in seconds; lists 3I/ATLAS |
| 2 | JPL Small-Body Identification API (NASA/JPL) | Asked for the tracks SkyBoT has no name for, and for all when SkyBoT does not answer. Takes minutes for the asteroids of a field and has no record of 3I/ATLAS (both observed on 2026-11-14) |

`known.checked` is `false` when neither service answered: "not known" is never
said without having asked, and the services are asked again later (step 9).
A track that the services were asked about and none knows has
`known.name = null`; it is a candidate, not a discovery.

## Votes

Visitors can say of each object whether its motion looks real or is a false
alarm. No account is needed: the browser makes up a random id once and sends
it with the vote.

- One vote per browser id and object; a new vote replaces the old one, `null`
  takes it back.
- At most 8 browser ids behind one network address may vote on the same
  object (`SPHEREX_MAX_VOTERS_PER_ADDRESS`): room for a team or a class on
  one network, not for a script. The address is stored as a salted hash only; the salt is a random
  file in the cache directory.
- At most 20 votes per minute per caller (`SPHEREX_RATE_VOTES_PER_MIN`).
- From 3 votes on, the log gives a verdict: `real` when real votes are at
  least twice the false ones, `false` in the opposite case, `disputed`
  otherwise.

## Data model

`movers.sqlite` in the cache directory (on the persistent disk in production):

| Table | Holds |
| --- | --- |
| `fields` | One row per searched field: centre, size, number of frames, algorithm version, the cells it covers, statistics of the search |
| `detections` | Every detection of every searched field: exposure, time, position, signal to noise, flux, detector, wavelength, sky cell |
| `movers` | One row per object: id (`VS-<MJD>-<4 characters>`), reference position and time, speed, first and last change, the full record as JSON |
| `votes` | Object, browser id, vote, hashed address, time |

The endpoints are described in the [API reference](api-reference.md#moving-objects).
