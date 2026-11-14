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
   its detections are removed. Pieces of one object are merged.
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
5. **Chance figure.** The same search is repeated with the exposure times of
   the detections shuffled. What it finds then is found by chance; the number
   per field is stored with the field as `stats.chance_tracks`.
6. **Names.** Each track is compared with the known asteroids and comets
   predicted for its place and time (see [below](#names-of-known-objects)).
7. **More is asked of a path without a name.** Nearly everything bright
   enough for SPHEREx is a known asteroid or comet, so a path that no
   catalogue has an object on is far more often chance than a new object. It
   is kept only when seen at 8 or more exposure times and at 80 % or more of
   the times covered (`stats.unnamed_dropped` counts the rest). In the viewer
   it is called a candidate, never a discovery, and is listed after the
   objects the catalogues confirm. A path that could not be compared because
   no catalogue service answered is kept under the rules of step 4 and shown
   as "not yet compared with the catalogues".

A track is a straight line at constant speed: reference time `mjd0`, position
`ra0`, `dec0`, and the motion `v_east`, `v_north` in arcsec per hour. Speed
classes: `slow` below 20, `fast` from 20, `very fast` from 60 arcsec per hour.

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

### The audit of 2026-11-14 (why the rules of step 4 and 7 exist)

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
step 7 asks of a path without a name.

Limits found in the same test:

- 3I/ATLAS at 8.7 arcsec per hour and V about 15.2 (field 3, March 2026) was
  not found: faint and slow, it moves less than a pixel between the exposures
  of one visit.
- An object seen in two passes days apart is stored as two tracks (Astraea
  and Nha above): a straight line does not describe several days of motion.
  Both carry the same name.
- The cores of bright moving objects are flagged in the Level 2 images, so the
  detections of a bright comet are its halo, and their positions scatter by a
  few arcsec (rms 7.7 arcsec for 3I/ATLAS in field 1).
- A search takes 30 to 55 seconds and about 1 GB of memory per field.

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
  in place of the old. Version 2 (2026-11-14) is the compact source test.
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
said without having asked. A track that both services were asked about and
neither knows has `known.name = null`; it is a candidate, not a discovery.

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
