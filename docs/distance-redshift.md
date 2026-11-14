# Distance and redshift

SPHEREx was built to look far back in time, and the first question about any
point of light is how far away it is. The product answers that in three
places:

1. **The object of a view.** What lies at the crosshair, and its distance (a
   star) or its redshift (a galaxy or a quasar), each with the catalogue and
   the paper it comes from.
2. **The sky map by redshift.** Galaxies and quasars drawn on the map, with a
   lowest redshift to show (for example only z > 5).
3. **Compare spectra.** The SPHEREx spectra of several objects on one plot, as
   observed or each moved to its rest frame by its redshift, with the strong
   emission lines marked.

Every distance and redshift here is a published value. Nothing is measured
from the SPHEREx images, and the interface says so wherever a value is shown.
The reasons are in [What is not done](#what-is-not-done-and-why).

- Lookup: `sphereblink/distance.py`, endpoint [`/api/object`](api-reference.md#get-apiobject).
- All-sky list: `web/data/redshift.json`, built by `scripts/build_redshift.py`.
- In the viewer: `web/depth.js`, `web/depth.css` ([features](features.md#18-distance-and-redshift)).

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

## What is not done, and why

**No redshift is derived from SPHEREx data.** The mission's own redshifts come
from fitting template spectra to full-depth 102-channel photometry (Doré et
al. 2014, arXiv:1412.4872; Stickley et al. 2016, arXiv:1606.06374; Feder et
al. 2024, arXiv:2312.04636) and that catalogue is not public. The published
work on real SPHEREx spectra so far concerns bright quasars: redshifts set
from the Hα line (Davies et al. 2026, arXiv:2603.10135), cross-correlation
with a quasar template (Stepney et al. 2026, arXiv:2605.06791), and stacks
shifted to the rest frame by external redshifts (Kim et al. 2026,
arXiv:2607.10282). The compare view follows the last of these: published z,
rest-frame shift, line markers. A redshift read automatically off our
quick-look spectra would need its own validation against known redshifts
before it could be shown as a result. The classical recipe for that
validation is in Colless et al. (2001, MNRAS 328, 1039): two independent
methods, at least two lines, and a quality flag.

**No age of a star.** A distance does not give an age. For a galaxy the
light-travel time is shown, which is how far back in time it is seen.

## References

- Gaia Collaboration 2023, A&A 674, A1 (Gaia DR3).
- Bailer-Jones et al. 2021, AJ 161, 147 (distances from Gaia parallaxes).
- Planck Collaboration 2020, A&A 641, A6 (cosmological parameters).
- Wenger et al. 2000, A&AS 143, 9 (SIMBAD).
- NASA/IPAC Extragalactic Database (NED), funded by NASA and operated by Caltech.
