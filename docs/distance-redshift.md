# Distance and redshift

SPHEREx was built to look far back in time, and the first question about any
point of light is how far away it is. The product answers that in four
places:

1. **The object of a view.** What lies at the crosshair, and its distance (a
   star) or its redshift (a galaxy or a quasar), each with the catalogue and
   the paper it comes from.
2. **The sky map by redshift.** Galaxies and quasars drawn on the map, with a
   lowest redshift to show (for example only z > 5).
3. **Compare spectra.** The SPHEREx spectra of several objects on one plot, as
   observed or each moved to its rest frame by its redshift, with the strong
   emission lines marked.
4. **A redshift from the lines.** Where the SPHEREx spectrum of an object
   shows emission lines, the redshift they give, next to the published one.

The distances and redshifts of the first three are published values, and the
interface says so wherever one is shown. The fourth is our own estimate. It is
kept apart, labelled as an estimate, and given only where it passes the checks
described in [A redshift from the lines](#a-redshift-from-the-lines).

- Lookup: `sphereblink/distance.py`, endpoint [`/api/object`](api-reference.md#get-apiobject).
- All-sky list: `web/data/redshift.json`, built by `scripts/build_redshift.py`.
- In the viewer: `web/depth.js`, `web/depth.css` ([features](features.md#18-distance-and-redshift)).
- Estimate from the lines: `web/redshift.js`, `web/data/quasar-template.json`
  (built by `scripts/build_quasar_template.py`), checked by
  `scripts/validate_redshift.py`.

## Two kinds of distance

| Object | What is looked up | Where |
| --- | --- | --- |
| Star | Parallax, and the distance derived from it | ESA Gaia DR3, with the geometric distance of Bailer-Jones et al. (2021); for stars too bright or too faint for Gaia, the parallax SIMBAD holds (Hipparcos and others) |
| Galaxy, quasar | Redshift z | SIMBAD (CDS), then NED (NASA/IPAC) |

A star has no cosmological redshift: its radial velocity is a few hundred
km/s at most, and it is shown as a velocity. A galaxy has no parallax. The
lookup decides which of the two applies from the object type SIMBAD gives.

From a redshift the lookup computes, with the Planck 2018 cosmology (flat
ΛCDM, H0 = 67.66 km/s/Mpc, Ωm = 0.3111; `astropy.cosmology.Planck18`):

- the light-travel time ("light set out 12.9 billion years ago"),
- the age of the universe at that time,
- the comoving distance today.

These are labelled as computed, with the name of the cosmology. Below
z = 0.01 they are not shown: the galaxy's own motion (hundreds of km/s) is
then as large as the expansion, and z is a velocity and not a distance. For
those galaxies the lookup returns the distances measured by other methods
that SIMBAD has collected from the literature (table `mesDistance`): the
median, the range, how many, and the newest with their papers.

## The lookup

`distance.lookup(target)`:

1. SIMBAD cone (5 arcsec around a named object, 10 arcsec around a picked
   point, which is under two SPHEREx pixels) and Gaia DR3 cone, in parallel.
   A named object with a known proper motion is looked up where it was at the
   catalogue's epoch (J2000 for SIMBAD, 2016.0 for Gaia). The Gaia query also
   returns stars moving faster than 400 mas/yr within 200 arcsec, so a fast
   star is found at the place it has reached today.
2. The SIMBAD entry is chosen: the one that carries the target's own name;
   else the nearest that has a redshift or a parallax; else the nearest.
   Planets, clouds, regions and bare detections (`X`, `IR`, `Rad`, ...) are
   not taken as the object.
3. Galaxy or quasar: its SIMBAD redshift, with error, quality letter, whether
   it is spectroscopic or photometric, and the bibcode. If SIMBAD has none or
   does not answer, NED is asked (nearest entry with a redshift,
   spectroscopic first).
4. Star: the Gaia source on the position (for a picked point, the brightest
   within one SPHEREx pixel). Distance from Bailer-Jones et al. (2021) when
   the ESA archive answers, else 1 / parallax where the parallax is at least
   5 times its error. Without a Gaia parallax, the parallax in SIMBAD.
5. A Gaia source with no parallax to speak of (below 1.5 sigma) that SIMBAD
   does not call a star gets no distance: it may be a quasar, which is a point
   of light to Gaia too. NED is asked about it. This case was found in
   testing: with SIMBAD switched off, 3C 273 came back as a "star at 17 kpc".

Fallbacks: SIMBAD has two mirrors (Strasbourg and Harvard) and NED behind
them; Gaia is read from ESA and, when that fails, from IRSA's copy. The answer
lists the services that did not respond (`unavailable`) and is only stored
when all of them did (`complete`). A lookup takes 1 to 6 s the first time and
comes from the store afterwards.

Checked against the live services on 2026-11-14:

| Target | Result |
| --- | --- |
| Barnard's Star | 1.828 pc (Gaia DR3, Bailer-Jones) |
| Vega | 7.68 pc (SIMBAD parallax, van Leeuwen 2007) |
| WISE 0855-0714 | 2.28 pc (SIMBAD parallax, Kirkpatrick et al. 2021) |
| 3C 273 | z = 0.1576, light set out 2.04 billion years ago |
| APM 08279+5255 | z = 3.911 |
| SDSS J0100+2802 | z = 6.327, 12.9 billion years |
| MoM-z14 | z = 14.44, 13.50 billion years |
| M 77 | z = 0.0035 (local); measured 14.3 Mpc, 4 published values |
| M 31 | z = -0.001 (a blueshift: no cosmological distance) |
| Empty sky | "nothing catalogued", not an error |

`tests/test_distance.py` replays recorded answers of the four services,
including the cases with a service down.

## The all-sky list

`scripts/build_redshift.py` asks SIMBAD (TAP) for galaxies and quasars with a
redshift marked spectroscopic, quality A to D:

- every object at z >= 5 (4 437 on 2026-11-14);
- below z = 5, in each of eight redshift ranges, the 700 brightest in the K
  band, the ones SPHEREx has the best chance to record.

10 037 objects, about 600 KB (200 KB compressed), loaded when the layer is
first switched on. Each row holds position, z, galaxy or quasar, K magnitude,
name and the paper of the redshift.

Two filters remove evident pipeline misfits above z = 5: galaxies whose
redshift comes from an SDSS data release paper, and anything brighter than
K = 15 (the brightest quasar known there, SDSS J0100+2802, has K = 15.2).
99 of 4 536 entries were removed. What remains is what SIMBAD lists, and a
wrong redshift can still be among it; that is why every object carries its
paper, and why the interface says "as listed by SIMBAD".

The map draws a galaxy as a dot and a quasar as a diamond, coloured by
redshift in ten steps from pale blue (near) through violet to red (far), and
dims the chart behind. The patches that show at high z are survey fields
(SDSS, HSC, the JWST deep fields), not structure of the universe.

Most objects above z = 5 are far too faint for a single SPHEREx exposure:
only the brightest quasars are recorded (SDSS J0100+2802, K = 15.2, is; see
below). The hover note says which objects are bright enough (K of 15.5 or
brighter).

## Compare spectra

The sheet plots, for up to six objects, the median SPHEREx flux in steps of
2% in wavelength (the aperture photometry of
[`/api/spectrum`](api-reference.md#get-apispectrum); points on flagged pixels
are left out). Objects load one after another so the server reads one view at
a time.

- **Observed / Rest frame.** At rest every wavelength is divided by (1 + z) of
  its object, on a logarithmic axis.
- **Relative / mJy.** Relative divides each spectrum by its own median.
- **Lines.** Lyα, C IV, C III], Mg II, Hβ, [O III], Hα, He I 1.083, Paβ, Paα,
  Brγ and the 3.3 µm PAH band.
- **z of each object.** Starts at the published value. It can be changed by
  hand to see where the lines fall; a value set by hand is labelled an
  estimate by eye.

The example "four quasars" was checked on real data on 2026-11-14:

| Quasar | z | Hα expected at | Seen in the SPHEREx spectrum |
| --- | --- | --- | --- |
| 3C 273 | 0.158 | 0.760 µm | at the blue edge of the band; Paα at 2.17 µm is clear |
| PG 1634+706 | 1.337 | 1.534 µm | peak 2.9 times the continuum at 1.53 µm; Paβ and Paα too |
| APM 08279+5255 | 3.911 | 3.223 µm | peak 2.7 times the continuum at 3.22 µm |
| SDSS J0100+2802 | 6.327 | 4.809 µm | peak 3.5 times the continuum at 4.79 µm; no flux below Lyα |

In the rest frame the Hα peaks of all four fall on the same marker. That is
the comparison of "early" and "recent" objects the feature was asked for: the
same hydrogen line, emitted 2 and 12.9 billion years ago.

## A redshift from the lines

`Redshift.estimate(points, template)` in `web/redshift.js` takes the points of
[`/api/spectrum`](api-reference.md#get-apispectrum) and gives a redshift with
a quality of 3 or 4, or says why there is none. It runs in the browser, on the
spectrum the viewer already has; the server does nothing more.

It follows the recipe of the 2dF Galaxy Redshift Survey (Colless et al.
2001): two independent methods, at least two lines, and a quality flag. The methods are those already used on real
SPHEREx spectra of quasars: lines placed on Hα (Davies et al. 2026) and
cross-correlation with a quasar template after the continuum is taken out
(Stepney et al. 2026, after Tonry & Davis 1979).

1. **Points.** Only direct measurements on unflagged pixels; fewer than 60 of
   them give nothing. The points are grouped by visit of the survey (SPHEREx
   comes back every six months and takes the whole spectrum in about two
   weeks).
2. **Continuum.** In each visit on its own, a straight line is fitted to the
   points within 11 % in wavelength of each place, without the points that
   stand out. What is left are the lines. Taking the visits apart matters:
   many quasars change between two visits, and the points of a bright visit
   among those of a faint one would stand out like lines. The noise of a point
   is the scatter of its 40 neighbours, never less than its quoted error, and
   no single point counts for more than 12 sigma.
3. **Cross-correlation.** The mean quasar spectrum is smoothed to the
   resolving power of SPHEREx (41, and 118 beyond 3.8 µm), its continuum is
   taken out the same way, and it is laid on the lines at every trial redshift
   from 0 to 6.6 in steps of 0.1 % in (1 + z). The strength of the match is
   counted in units of its noise.
4. **Line matching.** Independently, every peak 4 sigma above the continuum is
   found. A peak is kept only if it is narrow (a filter three times as wide
   must not see it better) and if it still stands at 2 sigma without the day of
   observation that shows it best. Every redshift is then listed that puts two
   kept peaks or more on known lines (Lyα, C IV, C III], Mg II, Hβ + [O III],
   Hα, He I 1.083, Paβ, Paα, Brα), with Hα found at its place and the strongest
   peak of the spectrum among the lines. The redshift with the most evidence
   in its lines is taken; its value is that of the line centres.
5. **Quality.** The cross-correlation has to agree: a match of 9 sigma at the
   redshift of the lines, the strongest line at 7 sigma, and the match there
   1.2 times stronger than at any other redshift (0.8 times is enough when
   four lines stand as peaks of their own). That is quality 3. Quality 4 asks
   for 12 sigma, 8 sigma and 1.5 times. A single line, or two methods that do
   not agree, is quality 2; no lines, quality 1. Qualities 1 and 2 show
   nothing.

Three of these rules are there for what SPHEREx data and the sky actually do:

- **A flare is not a line.** SPHEREx takes up to four neighbouring wavelengths
  within minutes. A blazar that was 60 % brighter that day leaves four points
  standing out together. They are of one day, so the peak does not stand
  without it.
- **A bump is not a line.** The spectrum of a brown dwarf has broad bumps
  between its water and methane bands. They are wider than any emission line
  at this resolving power, and the wide filter sees them better.
- **Hα has to be there.** It is the strongest line of a quasar from the
  visible to the infrared. A redshift that puts Hα inside the spectrum where
  nothing stands is not accepted, and above z = 6.6, where Hα has left the
  spectrum, no redshift is given.

The template is one mean quasar spectrum from 0.1 to 3.5 µm at rest
(`web/data/quasar-template.json`, 18 KB): the X-shooter composite of Selsing
et al. (2016) below 0.55 µm and the SDSS and IRTF composite of Glikman et al.
(2006) above 0.60 µm, both read from VizieR and blended in between.

### Where it shows

- **Card "Distance and redshift"** (Advanced): a row "From its lines" with the
  value, its error, the quality, and how far it is from the published value;
  a row "Lines found" with each line and the wavelength it was found at. With
  no published redshift, the light-travel time of the estimate is given. The
  note under the card says that this value is our own.
- **Simple**: only when there is no published redshift, one sentence that ends
  "Our own reading, not a published value."
- **Compare spectra**: "from its lines 1.343" next to the published value of
  each object; pressing it moves the spectrum to that redshift.

An object that SIMBAD lists as a star, or that has a parallax distance, is
never given one, whatever its spectrum looks like.

### How well it does

`scripts/validate_redshift.py` asks a local engine for the SPHEREx spectra of
objects with known answers and runs the same file the viewer loads. A redshift
counts as right within 2 % of the published value in (1 + z), which is less
than one resolution element. Checked on 2026-11-14:

@@TABLE@@

Read by redshift, the first sample says where the method works: every one of
the quasars it answered between z = 0.16 and z = 6.5 is right, and the median
distance from the published value is 0.2 % of (1 + z), the largest 1.0 %. The
ones it did not answer are blazars (no lines, and light that changes from day
to day), quasars with a single clear line, and those too faint for single
exposures.

| Object | Published z | From its lines | Quality | Lines found |
| --- | --- | --- | --- | --- |
| 3C 273 | 0.1576 | 0.157 ± 0.003 | 4 | He I, Paβ, Paα, Brα |
| PG 1634+706 | 1.337 | 1.343 ± 0.006 | 4 | Hβ + [O III], Hα, He I, Paβ, Paα |
| APM 08279+5255 | 3.911 | 3.871 ± 0.040 | 4 | C III], Mg II, Hβ + [O III], Hα |
| SDSS J0100+2802 | 6.327 | 6.316 ± 0.015 | 3 | Lyα, Hα |
| UHS J0439+1634 | 6.519 | 6.516 ± 0.028 | 4 | Lyα, Hβ + [O III], Hα |

The thresholds were set while looking at the first sample, so that sample is
not an independent test of them. The second sample and the stars and galaxies
were run only after the thresholds were fixed.

### Limits

- It answers for objects with strong emission lines that are bright enough
  for single exposures: in practice quasars brighter than about K = 15. A
  normal galaxy, whose redshift the mission will get from the shape of its
  full-depth spectrum, gets none.
- The error is that of the line centres, about 0.2 to 1 % of (1 + z). Broad
  lines can sit a few thousand km/s from the systemic redshift of a quasar
  (APM 08279+5255: 3.87 from its broad lines, 3.911 from its molecular gas).
- A value that passes the checks is still an estimate from quick-look
  photometry, not a published measurement, and the interface never shows it
  without saying so.

## What is not done, and why

**No redshift for objects without emission lines.** The mission's own
redshifts come from fitting template spectra to full-depth 102-channel
photometry (Doré et al. 2014, arXiv:1412.4872; Stickley et al. 2016,
arXiv:1606.06374; Feder et al. 2024, arXiv:2312.04636), which reaches normal
galaxies through the shape of their continuum; that catalogue is not public.
Our single exposures are too shallow for that, so the estimate above is
limited to lines. The compare view follows Kim et al. (2026,
arXiv:2607.10282): published z, rest-frame shift, line markers.

**No age of a star.** A distance does not give an age. For a galaxy the
light-travel time is shown, which is how far back in time it is seen.

## References

- Gaia Collaboration 2023, A&A 674, A1 (Gaia DR3).
- Bailer-Jones et al. 2021, AJ 161, 147 (distances from Gaia parallaxes).
- Planck Collaboration 2020, A&A 641, A6 (cosmological parameters).
- Wenger et al. 2000, A&AS 143, 9 (SIMBAD).
- NASA/IPAC Extragalactic Database (NED), funded by NASA and operated by Caltech.
- Colless et al. 2001, MNRAS 328, 1039 (2dF Galaxy Redshift Survey: two
  methods, two lines, quality flag).
- Tonry & Davis 1979, AJ 84, 1511 (redshifts by cross-correlation).
- Selsing et al. 2016, A&A 585, A87, and Glikman, Helfand & White 2006,
  ApJ 640, 579 (the two quasar composites of the template), from VizieR
  (Ochsenbein et al. 2000, A&AS 143, 23).
- Davies et al. 2026, arXiv:2603.10135, and Stepney et al. 2026,
  arXiv:2605.06791 (redshifts of quasars from real SPHEREx spectra).
