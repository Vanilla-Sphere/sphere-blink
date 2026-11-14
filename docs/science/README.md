# Vanilla Sphere: science and algorithms

Written on 2026-11-14 against branch `develop`. Part of the [product design documentation](../README.md) of Vanilla Sphere, by team Vanilla Sphere for the 2026 NASA Space Apps Challenge.

## 1. Scope and conventions

This document gives, for every processing step of Vanilla Sphere, the physical reason for the step and the algorithm that carries it out. Every rule and threshold was read from the source code on branch `develop` on 2026-11-14, and each section names the file and function, so any statement can be checked against the code.

| Topic | Section | Code |
| --- | --- | --- |
| SPHEREx data and how it is read | 2 | `sphereblink/sia.py`, `cutout.py` |
| Pictures: epochs, RGB simulation, differences | 3 | `sphereblink/render.py`, `web/app.js` |
| Photometry and the spectrum | 4 | `cutout.aperture_flux`, `sphereblink/wings.py` |
| Position, proper motion, parallax | 5 | `sphereblink/measure.py`, `refcat.py` |
| Blind search for moving objects | 6, 7 | `sphereblink/movers.py`, `known_objects.py` |
| Reading a spectrum in words | 8 | `web/insight.js`, `web/linelist.js` |
| Redshift from the lines | 9 | `web/redshift.js` |
| Distance | 10 | `sphereblink/distance.py` |
| Change over time, band by band | 11 | `web/overtime.js`, `measure.light_curve` |

Conventions used throughout:

- **Sigma is robust.** Noise is 1.4826 times the median absolute deviation, so stars and defects do not inflate it.
- **Three kinds of number.** Measured from SPHEREx pixels (fluxes, positions, motions, paths, the redshift from the lines); looked up in a published catalogue (parallax, published redshift, names of asteroids); computed from a published value (light-travel time). The interface labels each kind, and this document does too.
- **No learned model.** Every result comes from fixed rules on measured pixels. No machine-learning model runs in the product.
- **Validation figures** are those recorded in the subsystem documents (`docs/measurement-pipeline.md`, `docs/moving-objects.md`, `docs/distance-redshift.md`, `docs/imaging-engine.md`) on 2026-11-14.

## 2. SPHEREx data and how it is read

### 2.1 What one exposure is

SPHEREx has no spectrograph. Each of its six detector arrays (2040 x 2040 pixels of 6.15 arcsec, 3.5 degrees on a side) sits behind a linear variable filter: the wavelength that reaches a pixel depends on where the pixel is on the detector. One exposure of about 115 s is therefore an image in which every source is seen at one wavelength, set by where it fell on the detector. The telescope steps across the sky so that a source lands on a different part of the filter each time, and the exposures of one position together sample 0.75 to 5.0 µm.

| Detector | Range (µm) | Channels | Resolving power (mission design) |
| --- | --- | --- | --- |
| D1 | 0.75-1.09 | 17 | about 41 |
| D2 | 1.10-1.62 | 17 | about 41 |
| D3 | 1.63-2.42 | 17 | about 41 |
| D4 | 2.42-3.82 | 17 | about 35 |
| D5 | 3.83-4.41 | 17 | about 110 |
| D6 | 4.42-5.00 | 17 | about 130 |

Six detectors of 17 channels give the 102 channels of the mission. The product does not bin exposures into those 102 channels. Each exposure gives one point at the exact wavelength and bandwidth of the target's detector position, read from the file itself (section 2.4). An 8 arcmin view holds about 350 exposures, so a spectrum has several points per channel, each with the width of one channel.

**Cadence.** SPHEREx returns to a position about every six months and observes it for days to weeks (a *visit* or *sky pass*). Within a visit it takes about four exposures a few minutes apart on each orbit, and the orbits are 96 minutes apart. Every time-domain method below is built around this pattern: bursts of minutes, orbits of 96 minutes, visits six months apart.

### 2.2 Finding the exposures

`sia.find_exposures_at` asks the IRSA Simple Image Access v2 service which Level 2 images contain a point, once per release (`spherex_dr1`, then `spherex_qr3`; the first listed wins where both hold an exposure). Views up to 12 arcmin use the exposures that contain the centre. Wider views are mosaics and ask at a 3 x 3 or 5 x 5 lattice of points. If more than 1200 exposures cover a view they are thinned evenly in time.

### 2.3 Reading only the pixels needed

A Level 2 file is about 65 MB. The engine reads the few dozen detector rows it needs from the public AWS Open Data bucket `nasa-irsa-spherex` with four HTTP range requests (`cutout.cut_async`), 1.3 to 1.9 MB per exposure for an 8 arcmin view:

1. The first 34 560 bytes hold the primary and IMAGE headers, with the astrometry (TAN projection with SIP distortion).
2. The IMAGE extension is uncompressed 32-bit floats, so row `y` starts at a computable byte offset (header end + `y` x 8160 bytes).
3. The FLAGS extension is Rice-compressed one row per tile. Its index gives the offset and length of each row, so the matching rows are one contiguous range.

The result was compared with a cut from the fully downloaded file and is identical pixel for pixel. If the bucket does not answer, the same byte ranges are read from IRSA.

### 2.4 Wavelength of a pixel

The Level 2 files describe the filter with a spectral WCS: central wavelength and bandwidth as a function of detector position. `cutout.wave_map_from_file` evaluates it once per release and detector on a 41 x 41 lattice, and lookups interpolate linearly. The wavelength of a spectrum point is this map at the detector position of the target in that exposure.

### 2.5 From detector pixels to a frame

Applied in order to the rows that were read (`cutout.py`):

1. **Mask.** Pixels with any of the flags `TRANSIENT`, `OVERFLOW`, `SUR_ERROR`, `NONFUNC`, `MISSING_DATA`, `HOT`, `COLD`, `NONLINEAR`, `PERSIST`, `OUTLIER`, `SNOWBALL` become NaN. The flag `SOURCE` is kept: it marks pixels on known stars. Bit positions are read from each file's `MSKN` cards.
2. **Subtract the sky.** The background of a SPHEREx image is zodiacal light, which changes with the date and, through the filter, across the detector. A plane (level plus gradient) is fitted with iterative 2.5 sigma clipping and subtracted, so frames of different dates match. Cutouts smaller than 60 pixels get a constant level.
3. **Restore saturated cores for display.** Connected groups of four or more `OVERFLOW` or `NONLINEAR` pixels are cores of bright stars and are painted at the brightest level of the cutout. Lone overflow pixels are cosmic-ray hits and stay masked.
4. **Resample.** Bilinear interpolation onto a north-up tangent-plane grid centred on the target: 3.075 arcsec pixels (half native) for views up to 12 arcmin, native 6.15 arcsec for wider ones. Image and validity mask are interpolated separately and divided, so a masked pixel does not bleed into its neighbours. An output pixel is kept where more than 60 % of its weight is valid.

Surface brightness is in MJy/sr. One native pixel covers 8.89 x 10^-10 sr, so 1 MJy/sr in one pixel is 889 µJy.

## 3. Pictures: epochs, RGB simulation, differences

### 3.1 Epochs

Frames of one position differ in wavelength, background and noise, so they cannot be averaged as they are. `render.build_epochs` divides every frame by its own robust noise and then averages the frames of a group, ignoring missing pixels. The unit of an epoch image is therefore "sigma of the per-exposure sky noise", which is what makes frames of different bands comparable. Groups are single exposures, UTC days, or visits (a gap of more than 20 days starts a new visit). Frames with less than 20 % valid pixels are left out.

### 3.2 RGB simulation

Every SPHEREx band lies in the infrared, beyond 0.75 µm, where the eye sees nothing. The colour picture is therefore called "RGB simulation" and never true colour: three infrared bands are shown as red, green and blue so that a difference between bands is seen as a colour.

| Colour | Preferred detector | Fallback |
| --- | --- | --- |
| Red | D6 (4.42-5.00 µm) | D5 (3.83-4.41 µm) |
| Green | D4 (2.42-3.82 µm) | D3 (1.63-2.42 µm) |
| Blue | D2 (1.10-1.62 µm) | D1 (0.75-1.09 µm) |

The order of the wavelengths is kept, as in the eye: the longest in red, the shortest in blue. The six bands form three pairs (short, middle, long), one pair per colour, so the three colours span the whole range. In a pair the longer band is used, and the other stands in where it did not see the place (`render.RGB_CHANNELS`).

`render.build_color_epochs` then does three things:

1. **White balance.** Each detector is put on one display scale over the whole view: its median is subtracted and the result divided by the larger of its 99.7th percentile span and ten times its noise. The sky is then 0 and bright stars near 1 in every band, so bright stars come out near white. A source redder than them is relatively brighter at long wavelengths, as cool or dust-covered objects are.
2. **Borrowing.** SPHEREx sees a place with only one or two detectors on a given day. A channel not observed in an epoch is taken from the nearest epoch in time that has it, and masked pixels are filled from up to 8 neighbouring epochs of the same channel.
3. **Provenance.** `Epoch.channels` records, for R, G and B, the detector, the date, and whether the channel was borrowed. The viewer marks a borrowed channel, so a frame is never presented as a simultaneous three-colour observation.

### 3.3 Stretch

One pair of limits serves all epochs of a movie (1st to 99.7th percentile), so a change of brightness on screen is a change in the data. Colour channels start one sigma above the sky, or noise shows as coloured speckle. Brightness follows `asinh(10 t) / asinh(10)`, which keeps both faint sky and bright stars visible. In colour the curve is applied to the mean of the three channels and the channel ratios are kept, so a star keeps its colour from its wings to its core.

### 3.4 Differences and the deep image

Computed in the browser on the decoded cube (`web/app.js`), and by `render.difference` for exported GIFs:

| Mode | Operation | Why |
| --- | --- | --- |
| Minus median | Each epoch minus the per-pixel median of all epochs | A star is in the same pixel in every epoch and cancels. A moving object is in a pixel for a few epochs only, is absent from the median, and remains |
| Minus reference | Each epoch minus one chosen epoch | What changed since that date |
| Sum all | Mean of all epochs times the square root of their number | Noise of a mean falls as the square root of the number of epochs, so this is one deep image in units of its own noise |

### 3.5 Catalogue overlays

Gaia DR3 and CatWISE2020 sources are drawn on the frames, each moved by its proper motion to the date of the frame on screen (`sphereblink/catalogs.py`). A source is highlighted as a fast mover above 150 mas/yr when its motion is at least 5 times its error. CatWISE lists the halo, spikes and ghosts of a bright star as entries with large, confident, wrong motions (48 of the 250 brightest entries around Barnard's Star). `catalogs.flag_artifacts` marks such an entry as suspect and removes its motion when it is bright itself (W2 < 8.5), lies in the halo of a bright entry, carries an artifact flag, moves more than 15 arcsec/yr, or has four or more other fast entries within 200 arcsec.

## 4. Photometry and the spectrum

A spectrum in Vanilla Sphere is the aperture flux of the target in every exposure, plotted against the wavelength of that exposure (`/api/spectrum`). No dedicated spectral product is used. Photometry is done on the detector pixels, before resampling, because resampling correlates pixels and smears the undersampled point-spread function.

### 4.1 Aperture photometry

`cutout.aperture_flux`:

```
flux  = sum over aperture (pixel - sky) x 889 µJy per MJy/sr
sky   = median of the annulus
error = 1.4826 x MAD(annulus) x sqrt(number of aperture pixels) x 889
```

- **Aperture:** a circle of radius 2 native pixels (12.3 arcsec) at the target.
- **Sky annulus:** from 5 to 10 pixels. At least 20 valid pixels are required.
- **Error:** empirical. It is the scatter of the sky around the source scaled to the aperture area, so it includes what the archive's variance does not: faint neighbours and residual background.

### 4.2 Flagged pixels: when a flag is believed

The flags `OVERFLOW`, `TRANSIENT` and `SUR_ERROR` come from the on-board fit of the charge ramp. A source brighter than about 0.1 Jy sets them on its own core in most exposures, and the pixel still has a good value. Masking those pixels made the aperture read 2 to 5 times low. The rule, set against 2MASS on 38 isolated stars:

| Case | Test | Result |
| --- | --- | --- |
| Flagged core of a star | Only those three flags; brightest pixel within 1.5 pixels of the target, at most 2000 MJy/sr above the sky, holding at most 80 % of the aperture's light | Flagged pixels are summed at the archive's value |
| Cosmic-ray hit | One pixel holds more than 80 % of the light (a star puts at most 75 % in one pixel) | Flags are believed |
| Saturated core | A pixel has no value, or more than four pixels overflowed | No aperture flux; the halo method of 4.3 is used |
| Missing pixel near the centre | Masked pixel within 1.5 pixels of the target | No aperture flux |
| Missing pixel at the rim | Masked pixel further out | Left out of the sum; the point carries the mark `PARTIAL` |

With this rule the aperture flux is 0.95 to 0.99 of the 2MASS flux (medians, from 0.06 to 3.2 Jy at J and Ks), with an exposure-to-exposure scatter of 2 to 12 %.

### 4.3 Stars that saturate: flux from the halo

Stars brighter than about 2 to 3 Jy fill the wells of their central pixels below about 3.5 µm, and the archive gives those pixels no value. A few pixels out the same star is still there, hundreds of times fainter and within the linear range. The surface brightness of that halo in a ring is proportional to the star's flux (`sphereblink/wings.py`):

```
flux = (median ring level - ring offset) x factor(wavelength, ring) x 0.97 / excess(flux)
```

- **Rings** at 4-6, 6-9 and 9-13 native pixels. The innermost ring that is at least 80 % free of saturated pixels is used, because it has the most signal. The sky is the median between 22 and 30 pixels. The halo must stand 3 sigma above zero.
- **factor** is measured, not assumed: 16 wavelengths per ring, interpolated in log wavelength. `scripts/calibrate_wings.py` compared ring levels with aperture fluxes and 2MASS J, H, Ks on 81 isolated stars of K = 3 to 11 (29 000 exposures).
- **excess** corrects an instrument behaviour found in that calibration: above about 1 Jy the halo grows faster than the star (1.4 times at 5 Jy, 1.9 times at 20 Jy, the same in all three rings). The fitted trend is `max(0.86, 0.931 + 0.763 log10(flux in Jy))`. It follows the photon rate, so the flux is scaled by the resolving power of the channel relative to 41. Its cause is not established.
- **0.97** puts halo fluxes on the scale of unsaturated aperture fluxes.

After both corrections the halo fluxes of the calibrators agree with 2MASS to 0.90-1.06 in the median, with 11 to 25 % scatter from star to star (0.15 to 20 Jy). That scatter is reported as a systematic error added in quadrature to every halo point: 20 % up to 10 Jy, 25 % from 10 to 40 Jy, and 50 % with the flag `EXTRAPOLATED` above 40 Jy, where the trend is an extrapolation.

A halo holds about 1 % of a star's light, so anything else around the source counts many times over (a host galaxy, a dark cloud). The halo is therefore used only where the core truly has no value, and a lone halo point among direct ones (direct points outnumber it four to one within two bandwidths) is left out of the spectrum.

### 4.4 What a spectrum point carries

Wavelength, bandwidth, flux and error in µJy, method (`aperture` or `wings`), detector, time (MJD), exposure id and flags. Points with a defect flag (`HOT`, `PERSIST`, `PARTIAL` and others) are hidden on the plots by default and always kept in the CSV export.

## 5. Position, proper motion and parallax

For an object at a given position the pipeline measures where it is in every exposure and fits its motion across the sky (`sphereblink/measure.py`, `refcat.py`). It works on the native detector pixels, never on resampled pictures.

### 5.1 Where to look: the prediction

A star moves by its proper motion and swings back and forth each year by its parallax. `Body.at(mjd)` predicts the position on the date of each exposure:

```
position(t) = catalogue position + proper motion x (t - epoch) + parallax x P(t)
```

`P(t)` is the parallax factor, computed from the barycentric position of the Earth (Astropy's ephemeris). SPHEREx is 5 x 10^-5 au from the Earth's centre, which is negligible here. The catalogue values come from Gaia DR3 (the star within 4 arcsec of the position, epoch 2016.0). WISE 0855-0714, which Gaia cannot see, uses the AllWISE position of 2010.5 with the motion and parallax of Kirkpatrick et al. 2021. With no catalogue match the position itself is measured as an object of unknown motion.

Parallax cannot be left out. SPHEREx revisits a position every six months, from opposite sides of the Earth's orbit, so the parallax shift changes sign from visit to visit and would otherwise be absorbed into the proper motion.

### 5.2 Position in one exposure: a PSF fit

A stamp of 49 x 81 detector pixels is read around the prediction, masked, and its clipped median sky subtracted. `measure.fit_psf` then fits, by bounded least squares in a 7 x 7 pixel window:

- a circular Gaussian **integrated over each pixel**. The SPHEREx point-spread function is undersampled (6.15 arcsec pixels), so sampling the Gaussian at pixel centres would bias the position;
- a constant sky, and the Gaussian width as a free parameter between 0.3 and 2.5 pixels;
- **neighbour stars at fixed positions**: Gaia stars within 30 arcsec of the path are predicted for the date and fitted at the same time (up to four with free flux), so a brighter star beside the target does not pull the fit.

The position may move at most 1.5 pixels from the prediction. A fit that ends at that limit or at a width limit is rejected. A position is kept at signal to noise 6 or more. What is found more than 3 arcsec from the place asked for is reported as a neighbouring source, not as the target.

**Saturated stars.** When the core is masked, `fit_wings` fits a round Moffat profile to the wings in a 25 x 25 pixel window with a robust loss. This gives a position without a flux, used in the track only when fewer than 20 core positions exist.

### 5.3 Objects too faint for one exposure: co-moving stacks

Exposures are grouped by visit (gaps over 45 days) and detector. A group in which fewer than half of the exposures gave a detection is stacked: each stamp is resampled onto a 21 x 21 grid of half-native pixels centred on where the target should be **on that exposure's date**. A moving object then stays in the centre while background stars smear out. The stack is a 3 sigma clipped mean, fitted with the same model and accepted at signal to noise 5. Its errors come from a bootstrap: the fit is repeated on 16 stacks of exposures drawn with replacement, because pixels of a resampled stack are correlated and the formal errors are not valid. This is how WISE 0855-0714 is found: invisible in every single exposure, present in the 4 to 5 µm stacks.

### 5.4 The track fit

`measure.fit_track` fits, in arcsec east and north of a reference point, with `t` in Julian years:

```
east(t)  = E0 + pm_east  x (t - t0) + parallax x P_east(t)
north(t) = N0 + pm_north x (t - t0) + parallax x P_north(t)
```

| Aspect | Treatment | Reason |
| --- | --- | --- |
| Weights | Error of each position with a floor of 0.15 arcsec in quadrature | No single position is better than that on 6.15 arcsec pixels |
| Reference epoch `t0` | Weighted mean date | Decorrelates position and motion |
| Proper motion | Fitted with two or more visits spanning 0.25 years; otherwise the catalogue motion is held | One visit cannot measure a yearly motion |
| Parallax | Catalogue value held fixed | See the limit below |
| Outliers | Up to four rounds of rejection at 4 robust sigma | Cosmic rays, neighbours, bad pixels |
| Errors | The larger of the formal error (scaled by reduced chi-square) and a delete-one jackknife over groups of (visit, detector) | Captures offsets shared by a whole visit |

With three or more visits the parallax is also fitted freely and reported as a check only. The spacecraft is turned around between visits, and any small asymmetry of the point-spread function turns with it. That leaves an offset of 0.1 to 0.3 arcsec that changes sign each visit, exactly as parallax does.

### 5.5 What is reported

The fitted motion with its 1 sigma error next to the catalogue motion, its significance (speed divided by error), and its direction as a position angle from north through east. Below 3 sigma the viewer says "No significant motion detected" and gives an upper limit (speed + 3 sigma) with no direction. This is motion across the sky only: radial velocity would need Doppler shifts.

Checked on 2026-11-14 (mas/yr): Barnard's Star -992 +/- 472, +10327 +/- 249 against Gaia -802, +10362; Epsilon Indi B +3945 +/- 48, -2513 +/- 49 against +3982, -2467; WISE 0855-0714 -8766 +/- 992, +1536 +/- 992 against -8124, +673.

## 6. Blind search for moving objects

The search answers "what moves in this field?" with nothing known beforehand (`sphereblink/movers.py`, version 3). It runs on the frames a 50 arcmin field already has in the cache: several hundred exposures on one north-up grid of 6.15 arcsec pixels. It finds asteroids and comets, names the known ones, and holds back what the evidence does not support.

Two terms are specific to this section. A **visit** is one burst of exposures on one orbit (no gap above 0.5 hours). A **pass** is a run of visits with no gap above 2 days. Two detectors exposed at the same moment share one **exposure time**, and evidence is counted in exposure times, because two detectors exposed together are one piece of evidence.

### 6.1 Median sky subtraction

A star is in the same pixel in every frame. A moving object is in a given pixel for a few frames only. The per-pixel median of the frames therefore holds everything that stays and nothing that moves (`movers.static_sky`).

- One median **per detector**, because a star is not equally bright in every band. A pixel needs 8 frames of that detector, and 12 frames in all for its median to be trusted.
- Pixels where the median is 4 sigma or more above its own noise are stars and galaxies. They are masked, widened by one pixel, because their subtraction leaves residuals.

### 6.2 Detection at 5 sigma or more

In every frame minus its static sky (`movers.detect_frame`):

- Background and noise are measured locally, in tiles of 32 pixels, so a gradient or a noisy corner does not create or hide sources.
- A detection is a connected group of pixels with a peak of **5 sigma or more**, grown down to 2.5 sigma, of 2 to 400 pixels. One pixel alone is a cosmic-ray hit or a detector defect. More than 400 is background structure.
- A frame with more than 80 detections contributes none: it is a frame full of leftovers of stars, counted as a bad frame in the search statistics. Its pixels still take part in the pixel test of 6.4.
- **Recurring places are dropped** (`drop_recurring`): a detection that comes back within 6.2 arcsec at two or more other times, too far apart for anything moving at 2 arcsec per hour or faster to still be there, is a leftover of a star or a defect of the grid.

### 6.3 Linking: a line on the sky at constant speed

Over a few hours a solar system object moves on a straight line at constant speed. Linking looks for such lines among the detections of one pass (`movers.link`):

1. Every pair of detections up to 12 hours apart and at least 9 arcsec apart proposes a motion, if that motion is between 2 and 600 arcsec per hour.
2. All detections of the pass vote on each proposal. A detection supports it when it lies within 7.5 arcsec of the line (1.2 native pixels). The tolerance grows by 0.15 arcsec per hour away from the proposing pair, to at most 15 arcsec, because real paths are slightly curved.
3. A proposal is kept when detections at **4 or more exposure times in 2 or more visits** support it. One burst of exposures is not a track.
4. The best supported line is taken first, refitted on its members by least squares, and its detections are removed. A track must also cover 18 arcsec in total (three native pixels) and have a scatter of 7.5 arcsec or less.

Slower than 2 arcsec per hour, an object moves less than a pixel within a visit and cannot be told from a star's leftover. Faster than 600, it trails within one exposure.

### 6.4 Blob verification in the pixels

Four detections on a line happen by chance among thousands. The decisive test goes back to the pixels (`movers.confirm`, `sighting`): along a track, **every** exposure that covers the predicted place is measured there, including those in which nothing was detected. Only the detectors in which the track has a detection count, because an object may be invisible in the others. The object counts as seen in an exposure when both hold:

- the brightest pixel within two pixels of the predicted place is 3.5 sigma above the local noise, and
- that pixel with its eight neighbours is a compact source: together 3 sigma above their noise, and holding at least 1.3 times what the brightest pixel holds alone.

The second condition is the blob test. The telescope spreads the light of a real object over neighbouring pixels (1.4 to 3.4 times the brightest pixel, measured on real asteroids). A noise peak, a cosmic-ray hit or a star's leftover is one pixel. At 30 000 random empty places per field an exposure "sees" a compact source in 2.0 to 3.4 % of cases (5 to 12 % with the brightest pixel alone), and at JPL's positions of real asteroids in 70 to 76 %.

A track is kept when the object is seen at **6 or more exposure times, at 70 % or more of the times its path was covered, and in half or more of the covering exposures**.

### 6.5 Version 3: curved paths over several days

Over days the apparent motion changes, mostly because the telescope goes round the Sun with the Earth, and a straight line misses by tens of arcsec. A path of 20 hours or more, seen at 6 or more times in 3 or more visits, is also fitted with a constant acceleration:

```
east(t)  = v_east  x t + a_east  x t^2 / 2
north(t) = v_north x t + a_north x t^2 / 2      (t in hours from the reference time)
```

The curve replaces the line only when it leaves less than 70 % of the line's scatter, at least 1 arcsec less, and needs no more than 0.2 arcsec per hour squared (measured: 0.03 for Astraea, 0.04 for Nha). Two confirmed pieces up to 48 hours apart are one object when one curve holds both within 7.5 arcsec rms. Pieces are joined only **after** the pixels have confirmed each of them: the long path of a faint object crosses visits in which it is too faint to see, and would fail the pixel test as a whole.

### 6.6 Version 3: the three-visit rule for a path without a name

Nearly everything bright enough for SPHEREx is a known asteroid or comet, so a path that no catalogue has an object on is far more often chance than a new object. More is asked of it (`movers.strong`): seen at **8 or more exposure times, at 80 % or more of the times covered, and in 3 or more visits with 2 or more exposure times in each**.

The third visit is there because of a false path the other tests cannot see, the **static pair**. A leftover of a star stays at one place through the 2 to 4 exposures of one visit. A second leftover does the same in another visit. A line through the two places is a "path" seen at up to 8 of 8 exposure times, and the pixel test only measures the same two bursts again. In one field of the Milky Way plane (RA 281.22, Dec -2.58) eleven such paths passed linking and the pixel test. All eleven were seen in exactly two visits. Three leftovers on one line at the right times is far less likely, and section 7 measures how often it happens.

### 6.7 Matching with JPL and SkyBoT

Each track is compared with the known solar system objects predicted for its place and time (`sphereblink/known_objects.py`). One request per pass asks for the objects in the field, and the rates in the answer carry each object to the time of each track.

| Order | Service | Notes |
| --- | --- | --- |
| 1 | SkyBoT cone search (IMCCE, Observatoire de Paris) | Answers in seconds; lists 3I/ATLAS |
| 2 | JPL Small-Body Identification API (NASA/JPL) | Asked for tracks SkyBoT has no name for, and for all when SkyBoT does not answer |

A track takes the name of an object when both hold:

- **Motion agrees:** the difference is below 25 % of the speed, or 4 arcsec per hour.
- **Position agrees:** within 30 arcsec plus a tenth of the hourly motion, or within 300 arcsec for objects faster than 60 arcsec per hour. Predictions are for the centre of the Earth, and SPHEREx flies 700 km above it, so a fast, nearby object is seen off its predicted place.

The outcome decides what is shown:

| Catalogues say | Evidence | Shown as |
| --- | --- | --- |
| A known object is there with that motion | Kept by 6.4 | The object, by name |
| Nothing known there | Passes 6.6 | A candidate, never a discovery, listed after the named objects |
| Nothing known there | Fails 6.6 | Not shown (`stats.unnamed_dropped`) |
| No service answered | Passes 6.6 | "Not yet compared with the catalogues" |
| No service answered | Fails 6.6 | Stored but hidden until the services answer (`stats.waiting`) |

"Not known" is never said without having asked: `known.checked` is false until a service has answered.

## 7. Validation of the moving-object search

A search that reports paths has to answer two questions: how often does it report a path where nothing moves, and how many of the objects that are there does it find. The first is measured by shuffle tests that run with every search. The second is measured by injection and recovery.

### 7.1 Shuffle tests: what chance alone produces

The idea is a null test. Keep the pictures, destroy the time order, and with it every real motion. Whatever the same search still finds was found by chance (`movers.chance_tracks`).

| Test | What is shuffled | What is counted | What it measures |
| --- | --- | --- | --- |
| Exposure shuffle | The times of single exposures, within each pass | All tracks that pass linking and the pixel test (`stats.chance_tracks`) | Chance alignments of unrelated detections |
| Visit shuffle | Whole visits change places; each keeps its exposures in order | Only tracks with the evidence of the three-visit rule (`stats.chance_candidates`) | Alignments of leftovers that persist through a visit, which the first test tears apart and cannot see |

The visit shuffle is run without the detections of paths already seen in three or more visits, because an object that really moves keeps part of its path when visits change places and would be counted as chance.

Results on 2026-11-14 with version 3, twelve fields (ecliptic, high latitude, Milky Way plane and bulge), six shuffles of each kind per field:

- Exposure shuffle: 4 chance paths in 72 searches, each seen at 6 to 7 exposure times in two visits. That is enough for a known object and not enough for a path without a name.
- Visit shuffle: 0 paths with the evidence of the three-visit rule in 96 searches.
- In the Milky Way plane field, 11 static-pair paths passed the pixel test and none is shown.

### 7.2 Comparison with ephemerides

Three fields along the path of comet 3I/ATLAS gave 14 tracks, all known objects: 3I/ATLAS in two fields (174.9 and 99.4 arcsec per hour) and ten asteroids of visual magnitude 11.8 to 17.9. The nine named entries of the production log were compared detection by detection with JPL's ephemeris through IRSA's Moving Object Search Tool: 129 of 129 detections lie in exposures JPL says cover the object, at a median of 1.4 to 3.4 arcsec from the predicted position, with speeds agreeing to 0.1 arcsec per hour (0.7 for one object).

### 7.3 Injection and recovery: completeness

Surveys measure completeness by adding objects they know to their own data and counting how many their software finds. The tool for this is written (`sphereblink/mover_completeness.py` on branch `feat/mover-completeness`). On 2026-11-14 it was not yet merged and its results were not final, so no completeness figure is quoted here. Its method:

1. **Real exposures.** Every exposure of a field, with its real stars, noise, masked pixels and cosmic-ray hits. Nothing about the field is simulated.
2. **Fake objects.** Twelve per round, each on a straight line at constant speed, at a random place, time and direction, with a speed of 2 to 400 arcsec per hour (uniform in the logarithm) and a visual magnitude of 15.5 to 19.8.
3. **Brightness per exposure.** Every exposure has another wavelength. Two kinds of fake are used in turn. One is a main-belt asteroid: reflected sunlight (the colours of the Sun of Willmer 2018) plus its own heat at 2.7 au from the Sun, from the standard thermal model NEATM. The other is a body without heat of its own: reflected sunlight only.
4. **Shape.** A Gaussian spot integrated over detector pixels at a random offset and angle, then interpolated onto the grid with the engine's own rule, with the trail of its motion during the exposure. The width is set so the brightest pixel holds the share of light it holds for real stars.
5. **Injected first.** Fakes are added before the search starts, so the loss of a slow object into the median sky is part of the result.
6. **The unchanged search** runs on the frames. A fake is found when a returned path passes within 20 arcsec of its true place with its motion (within 15 % or 3 arcsec per hour). Two levels are counted: the evidence asked of a known object, and the evidence asked of a path without a name.

The injection test also found the static pair independently: two injected sources that hardly move, 11 hours apart, came out as one path of 75.6 arcsec per hour.

## 8. Reading a spectrum in words

`Insight.analyseSpectrum` in `web/insight.js` turns the points of a spectrum into sentences: what the curve shows, with the number behind each statement, how sure it is, and what limits apply. Nothing is learned from examples. Every statement is a fixed test on the measured points, and every wavelength it looks for is in `web/linelist.js` with its source. The result is a description, not a classification: at a resolving power of 35 to 130 and with a 12 arcsec aperture many objects look alike.

### 8.1 The tests, in order

1. **Which points can be read.** Fewer than 12 points give no reading. Halo points and direct points are compared at the same wavelengths. If their ratio is outside 0.75 to 1.33 only one kind is read, because the halo is then not that of one point source.
2. **Levelling the visits.** A source that changed between visits leaves steps wherever two visits meet, and a step looks like a band. Each visit is compared with the best-sampled one at the same wavelengths and brought to its level when at least eight pairs show a change of more than 10 % and their middle half agrees on its direction.
3. **Is anything there.** With fewer than a quarter of the wavelength bins above 3 sigma, the reading says that no source stands out and gives the 3 sigma limit of one exposure.
4. **The continuum.** The spectrum is binned into 28 logarithmic bins. Three things are measured: where it peaks; the slope `a` in `F_nu ~ wavelength^a` (median over all pairs of bins) over the whole range, below 1.7 µm and beyond 2.3 µm; and the closest blackbody between 300 and 20 000 K with the range that fits nearly as well. The blackbody is a colour temperature: dust in front of the source lowers it.
5. **Bands.** Each absorption band is the flux inside its window against a continuum drawn as a straight line in log-log between a window on each side. A band counts when it is at least 12 % deep at 4 sigma (15 % for water ice and the 2.7 µm band, 30 % for methane; 8 % at 2.5 sigma is "weak"). No band is claimed without a continuum 3 sigma above zero on both sides.
6. **Lines.** A narrow line is the median of the points whose passband holds the wavelength against the points 0.7 to 3.5 bandwidths away on both sides. A line counts when it stands 1.2 times the continuum at 4 sigma.

### 8.2 Rules that prevent false claims

| Rule | Reason |
| --- | --- |
| Around 3 µm, water vapour (2.7), water ice (3.0) and methane (3.3) overlap; the deepest place decides which is named | Three absorbers share one dip at this resolving power |
| With deep molecular bands, no emission line is claimed | Between deep bands the continuum stands up in narrow peaks (near 1.08, 1.27 and 2.1 µm) that no test at this resolution tells from lines |
| Water needs two bands, or one band with methane | One dip can be many things |
| Lines closer than 1.3 channels are one measurement, given to the strong line of a series | SPHEREx cannot split them |
| A `minor` line (Pa δ, Pf γ, the weaker line of a doublet) is named only when the reading is already "emission" | It is never seen without the strong ones; alone it is a bump |
| "Emission" needs two strong hydrogen lines, or one with another strong line beside it or 1.6 times the continuum | One line alone could be another line moved there by redshift |
| With a redshift of 0.01 or more, lines are tested at rest wavelength x (1 + z), and the bands of stars and ices are skipped | Those bands belong to a source at rest |

The reading then names the kind of curve, first match wins: saturated, emission lines, ice bands, brown dwarf (methane with water), cool star (water bands), partial coverage, flat, two components (extra light beyond 4 µm), red and rising, hot and falling, or a cool photosphere peak. Each comes with the objects that give such a curve, a confidence, and the list of everything that was looked for and not found.

### 8.3 The line list and its sources

All 32 reference wavelengths are in `web/linelist.js`; nothing is fetched at run time. Each number was read in its source on 2026-11-14. Atomic lines are vacuum wavelengths, as SPHEREx wavelengths are.

| What | Entries | Source |
| --- | --- | --- |
| Atomic emission lines: H I (Lyman α, Balmer, Paschen, Brackett, Pfund), He I, C IV, C III\], Mg II, \[O III\], \[S III\], \[Fe II\] | 25 | NIST Atomic Spectra Database 5.12 (Kramida et al. 2024), Ritz wavelengths |
| Water vapour at 1.38 and 1.88 µm | 2 | Band origins of Polyansky et al. 2018 (MNRAS 480, 2597, Table 1) |
| Water vapour at 2.70 µm, methane at 3.31 µm | 2 | Fundamental vibrations of Shimanouchi 1972 (NIST Chemistry WebBook) |
| Water ice at 3.0 µm, CO2 at 4.27 µm | 2 | Boogert, Gerakines and Whittet 2015 (ARA&A 53, 541, Table 1); McClure et al. 2023 |
| PAH band at 3.29 µm | 1 | van Diedenhoven et al. 2004 (ApJ 611, 928); Draine and Li 2007 |

Papers that show each band in the kind of object the reading names are kept beside it (Geballe et al. 2002 and Cushing et al. 2005 for brown dwarfs, Yamamura et al. 2010 for CO2). HITRAN was considered for the molecular bands and withdrawn: the reading needs band centres, which HITRAN does not print as such, so each band cites a source in which the number can be read directly. The windows in which a band and its continuum are measured are our own and are part of each entry.

## 9. Redshift from the lines

The expansion of the universe stretches every wavelength of a distant source by the same factor:

```
observed wavelength = rest wavelength x (1 + z)
```

So a set of emission lines keeps its pattern and slides along a logarithmic wavelength axis. `Redshift.estimate` in `web/redshift.js` reads z off a SPHEREx spectrum where emission lines allow it. It runs in the browser on the spectrum the viewer already has. The result is our own estimate, always shown next to the published value and labelled as such.

The recipe is that of the 2dF Galaxy Redshift Survey (Colless et al. 2001): **two independent methods, at least two lines, and a quality flag**. The two methods are those already used on real SPHEREx quasar spectra: lines placed on Hα (Davies et al. 2026) and cross-correlation with a template (Stepney et al. 2026, after Tonry and Davis 1979).

### 9.1 Preparing the spectrum

1. **Points.** Only direct aperture measurements on unflagged pixels. Fewer than 60 give nothing.
2. **Continuum, visit by visit.** In each visit on its own, a straight line is fitted to the points within 11 % in wavelength of each place, without the points that stand out. What is left are the lines. Taking visits apart matters: many quasars change between two visits, and the points of a bright visit among those of a faint one would stand out like lines.
3. **Noise.** The noise of a point is the scatter of its 40 neighbours, never less than its quoted error. No single point counts for more than 12 sigma, because garbage values occur in the data.

### 9.2 Method A: cross-correlation

A mean quasar spectrum from 0.1 to 3.5 µm at rest (`web/data/quasar-template.json`: the X-shooter composite of Selsing et al. 2016 below 0.55 µm, the SDSS and IRTF composite of Glikman et al. 2006 above 0.60 µm) is smoothed to the resolving power of SPHEREx (41, and 118 beyond 3.8 µm). Its continuum is taken out the same way. It is then laid on the observed lines at every trial redshift from 0 to 6.6, in steps of 0.1 % in (1 + z). The strength of the match at each redshift is counted in units of its noise.

### 9.3 Method B: line matching

Independently, every peak 4 sigma above the continuum is found. A peak is kept only if:

- **it is narrow:** a filter three times as wide must not see it better. The broad bumps between the water and methane bands of a brown dwarf fail here;
- **it stands without its best day:** it must still be 2 sigma without the day of observation that shows it best. SPHEREx takes up to four neighbouring wavelengths within minutes, so a blazar that flared that day leaves four points standing out together. They fail here.

Every redshift is then listed that puts two or more kept peaks on known lines (Lyα, C IV, C III\], Mg II, Hβ + \[O III\], Hα, He I 1.083, Paβ, Paα, Brα) within half a resolution element, with the strongest peak of the spectrum among them. **Hα must be found** whenever the redshift puts it inside the spectrum (z above 0.155): it is the strongest line of a quasar from the visible to the infrared. Above z = 6.6 Hα has left the spectrum and no redshift is given. The redshift with the most evidence in its lines is taken, and its value is that of the line centres.

### 9.4 Quality 1 to 4

The two methods must agree within 1.3 % in (1 + z).

| Quality | Condition | Shown |
| --- | --- | --- |
| 4 | Match of 12 sigma at the redshift of the lines, strongest line 8 sigma, match there 1.5 times stronger than at any other redshift | Yes |
| 3 | Match of 9 sigma, strongest line 7 sigma, match 1.2 times stronger than elsewhere (0.8 times when four lines stand as peaks of their own) | Yes |
| 2 | A single line, or two methods that do not agree | No |
| 1 | No lines | No |

The error given is the largest of three: the disagreement of the line centres, what the noise allows, and 0.4 % of (1 + z). An object that SIMBAD lists as a star, or that has a parallax distance, is never given a redshift.

### 9.5 How well it does

A redshift counts as right within 2 % of the published value in (1 + z), less than one resolution element (`scripts/validate_redshift.py`, 2026-11-14):

| Sample | Spectra | Given a redshift | Right | Wrong |
| --- | --- | --- | --- | --- |
| Quasars, first sample (used to set the thresholds) | 100 | 44 | 44 | 0 |
| Quasars, second sample (read after the thresholds were frozen) | 89 | 50 | 50 | 0 |
| Stars, brown dwarfs, young stars, nebulae | 35 | 0 |  | 0 |
| Nearby galaxies | 24 | 6 | 6 | 0 |

The median distance from the published value is 0.2 % of (1 + z), from z = 0.12 to 6.5. In the second sample two estimates stood far from the value SIMBAD lists (4.344 and 4.420 against our 2.406 and 2.433). The SDSS quasar catalogue DR16Q gives 2.413 and 2.435: the SIMBAD values are misfits of an automatic pipeline, and the lines caught them.

### 9.6 When the lines overrule the catalogue

Where an estimate of quality 4 and a published redshift of 0.01 or more differ by more than 2 % in (1 + z), the published value keeps its row and source, marked as disagreeing. Times and distance are then computed at the measured redshift, with the published figure beside it, and a second catalogue (NED) is asked. A page that doubts a value does not go on computing with it.

## 10. Distance

Distance is not measured from SPHEREx images. It is looked up or computed from published values, and every value names its catalogue and paper (`sphereblink/distance.py`, endpoint `/api/object`). Two physically different methods apply, and the lookup decides between them from the object type SIMBAD gives.

### 10.1 Stars: parallax

As the Earth goes round the Sun a nearby star appears to shift against the background. The angle of that shift is the parallax, and the distance follows from geometry alone:

```
distance in parsec = 1 / parallax in arcsec        (1 pc = 3.2616 light-years)
```

| Order | Source | Used when |
| --- | --- | --- |
| 1 | Geometric distance of Bailer-Jones et al. 2021, from Gaia DR3 | The ESA archive answers. It handles the error of the parallax properly, which a plain inverse does not |
| 2 | 1 / parallax from Gaia DR3 | The parallax is at least 5 times its error |
| 3 | The parallax SIMBAD holds (Hipparcos and others) | Stars too bright or too faint for Gaia, such as Vega and WISE 0855-0714 |

A fast star is looked up where it was at the catalogue's epoch (J2000 for SIMBAD, 2016.0 for Gaia), and the Gaia query also returns stars moving faster than 400 mas/yr within 200 arcsec, so a star that has moved since 2016 is still found. A Gaia source with no parallax to speak of (below 1.5 sigma) that SIMBAD does not call a star gets no distance: it may be a quasar, which is a point of light to Gaia too.

A star has no cosmological redshift. Its radial velocity is shown as a velocity. A star listed as faster than 100 km/s has that value compared with every measurement SIMBAD collects, and the median is shown instead when the listed value stands alone (more than 50 km/s and ten times their scatter from the median). This caught a catalogue misprint for tau Sco on 2026-11-14.

### 10.2 Galaxies and quasars: redshift and the Planck 2018 cosmology

A galaxy has no measurable parallax. Its redshift z is looked up in SIMBAD, then NED, with its error, whether it is spectroscopic or photometric, and the paper. A redshift becomes a time and a distance only through a model of the expansion. The lookup uses the Planck 2018 cosmology (flat ΛCDM, H0 = 67.66 km/s/Mpc, Ωm = 0.3111; `astropy.cosmology.Planck18`) and computes:

| Quantity | Meaning |
| --- | --- |
| Light-travel time | How long ago the light set out |
| Age of the universe then | The age at which the object is seen |
| Comoving distance | Where the object is today, after the expansion since |

```
comoving distance = (c / H0) x integral from 0 to z of dz' / E(z')
E(z) = sqrt( Ωm (1 + z)^3 + ΩΛ )      (radiation and neutrinos included by Astropy)
```

These values are labelled as computed, with the name of the cosmology. For example z = 6.327 (SDSS J0100+2802) is a light-travel time of 12.9 billion years.

**Below z = 0.01 none of this is shown.** A galaxy's own motion (hundreds of km/s) is then as large as the expansion, so z is a velocity and not a distance. M 31 even has a blueshift. For those galaxies the lookup returns the distances measured by other methods that SIMBAD has collected from the literature: the median, the range, and the newest with their papers.

### 10.3 What SPHEREx itself can and cannot add

The redshift of a quasar can be read from its SPHEREx lines (section 9), and the times are then computed at that value. The distance of a star cannot be measured from SPHEREx data: the free parallax of section 5.4 is limited by a pass-dependent offset of 0.1 to 0.3 arcsec, larger than the parallax of all but the nearest stars. A distance also does not give the age of a star, and none is shown.

## 11. Change over time, band by band

The "Over time" plot shows the flux of every exposure against its date, and a line under it says in words whether the object changed (`web/overtime.js`; the same method runs on the server in `measure.light_curve`).

### 11.1 The problem: every exposure has another wavelength

Two exposures of the same star on different dates see it at different wavelengths, so their fluxes differ even if the star never changed. The spread of fluxes inside one visit is the object's spectrum, not variability. Fluxes of different dates can only be compared after the spectrum is divided out.

### 11.2 Dividing by the object's own spectrum

`brightnessChange`:

1. **One kind of point.** Aperture fluxes and halo fluxes of saturated stars are not on one scale, so the kind with more points is used. With aperture fluxes, a median signal to noise below 3 gives "Too faint to compare". Fewer than 30 points give no answer.
2. **Visits.** Exposures are grouped into visits (gaps of more than 20 days). A long stay, as near the ecliptic poles, is cut into parts of about 30 days.
3. **Relative flux.** Points are sorted by wavelength. Each flux is divided by the median of its neighbours in wavelength taken from all dates (a running window of at least 9 points). That median is the object's mean spectrum there. The ratio is 1 when the object is at its average brightness, whatever the wavelength.
4. **Iteration.** A bright visit would pass for a bump in the spectrum. So the level of each visit is taken out before the spectrum is formed again, four times over.
5. **Comparison.** Each visit with 12 or more points gets its median relative flux and a random error from the robust scatter. The brightest visit is set against the faintest.

```
total error of a visit = sqrt( random error^2 + (floor x level)^2 )
sigma = ln(brightest / faintest) / sqrt( (error_hi / level_hi)^2 + (error_lo / level_lo)^2 )
```

The **floor** is what one visit is good to, however many exposures it has: 5 % for aperture fluxes and 12 % for halo fluxes in the viewer (2 % and 12 % in the server pipeline, which uses PSF-fit fluxes). The 2 % and 12 % were measured on stars that do not vary. "Brightness changed" needs **3 sigma**.

### 11.3 The verdict per band

An object can change colour: brighter at one end of the spectrum and fainter at the other. All bands together then nearly cancel, and the test above rightly says that the total did not change. `bandChanges` therefore repeats the same test inside each of the six detector bands, using only the points of that detector.

A band counts from **4 sigma, not 3**. Six bands are six chances of a false alarm, so the bar for each is higher. The verdict names the bands that faded and those that brightened, and ends with what all bands together show.

| Object | All bands together | Band by band |
| --- | --- | --- |
| R Draconis (Mira variable) | About 1.9 times between June 2025 and June 2026, 3.8 sigma | Changed as a whole |
| RW Aurigae (young star with moving dust) | 16 %, 1.9 sigma: no significant change | D5 and D6 up to 2.0 times brighter in September 2025 (9.0 sigma); D1 and D2 up to 1.6 times brighter in September 2026 (5.7 sigma) |
| AA Tauri (known for its dips) |  | D1 alone, 2.8 times, 11.6 sigma |
| 17 objects not known to vary | Steady | No band above 2.3 sigma |

The last row is the false-alarm check, on 2026-11-14: nearby stars, a white dwarf, planet hosts, a galaxy, two quasars and two brown dwarfs, read from the live service.

### 11.4 The time axis

Exposures fall into a few groups of days with empty months between them. `groupedAxis` gives each group its own stretch of the axis and cuts the empty months out, marked on the plot. No line is drawn across a cut.

## 12. Known limits

Each limit below follows from the instrument or from a choice made in the code. None is hidden in the interface.

| Area | Limit | Cause |
| --- | --- | --- |
| Spectrum | A point is one exposure at one wavelength, at a resolving power of 35 to 130. Narrow lines are blended and lines closer than 1.3 channels are one measurement | Linear variable filter, 102 channels |
| Spectrum | Everything within the 12 arcsec aperture is summed: a neighbour or a host galaxy is in the flux | 6.15 arcsec pixels |
| Spectrum | The points were taken on different dates. A variable source needs its visits levelled before bands are read | Survey cadence |
| Photometry | Saturated stars (above about 2 to 3 Jy below 3.5 µm) are measured from the halo, good to 20 to 25 %, and to 50 % above 40 Jy | Detector full well; the halo trend above 40 Jy is an extrapolation and its cause is not established |
| Photometry | Halo rings are centred on the view centre, not on the star's position in each frame (about one pixel of drift for Barnard's Star) | Implementation |
| Astrometry | A single position is good to 0.2 to 0.5 arcsec. Proper-motion errors are tens to hundreds of mas/yr | Undersampled point-spread function; three or four visits over little more than a year |
| Astrometry | Parallax is not measured independently | A pass-dependent offset of 0.1 to 0.3 arcsec changes sign exactly as parallax does |
| Astrometry | A stack needs a predicted path. Motion space is not searched | Design |
| Moving objects | Nothing slower than 2 arcsec per hour is found. 3I/ATLAS at 8.7 arcsec per hour and V about 15.2 was missed in one field | It moves less than a pixel within a visit, and partly ends up in the median sky |
| Moving objects | An object no catalogue lists that is seen in only two visits is not shown | The price of the three-visit rule against static pairs |
| Moving objects | Positions of bright comets scatter by a few arcsec (rms 7.7 arcsec for 3I/ATLAS in one field) | Their flagged cores leave only the halo to detect |
| Moving objects | Completeness as a function of brightness and speed is not yet stated | The injection test was not merged on 2026-11-14 |
| Moving objects | A shift-and-stack search for faint, slow objects is not part of the product yet | In development; not merged on 2026-11-14 |
| Spectrum reading | A description, not a classification. It can say "water and methane bands of a brown dwarf atmosphere" and cannot give a spectral type | Resolution and aperture |
| Redshift | Given only for objects with strong emission lines that are bright enough for single exposures: in practice quasars brighter than about K = 15. Not for normal galaxies, blazars, or quasars with a single clear line | Single exposures are shallow; the rules ask for two lines and two methods |
| Redshift | Broad lines can sit a few thousand km/s from the systemic redshift. Below z = 0.1 six Seyfert galaxies came out low by 0.3 to 0.5 % of (1 + z), a systematic not yet traced | Physics of quasar lines; open question |
| Distance | Looked up, not measured. A catalogue can be wrong, so every value carries its paper | SPHEREx data cannot give a distance to a star |
| Over time | Three or four visits about six months apart. Changes faster than a visit, or between visits, are not sampled | Survey cadence |
| Over time | A visit is not trusted to better than 5 % (aperture) or 12 % (halo) | Floors of the method |

## 13. References

**Data and services**

- SPHEREx Level 2 spectral images, releases DR1 and QR3: NASA/JPL-Caltech/IPAC, served by the NASA/IPAC Infrared Science Archive (IRSA) and the AWS Open Data programme.
- Gaia Collaboration 2023, A&A 674, A1 (Gaia DR3).
- Marocco et al. 2021, ApJS 253, 8 (CatWISE2020).
- Skrutskie et al. 2006, AJ 131, 1163 (2MASS).
- Wenger et al. 2000, A&AS 143, 9 (SIMBAD); NASA/IPAC Extragalactic Database (NED).
- Berthier et al. 2006, ASP Conf. Ser. 351, 367 (SkyBoT); NASA/JPL Solar System Dynamics, Small-Body Identification API.
- Astropy Collaboration 2022, ApJ 935, 167.

**Methods**

- Kirkpatrick et al. 2021, ApJS 253, 7 (astrometry of WISE 0855-0714).
- Bailer-Jones et al. 2021, AJ 161, 147 (distances from Gaia parallaxes).
- Planck Collaboration 2020, A&A 641, A6 (cosmological parameters).
- Colless et al. 2001, MNRAS 328, 1039 (2dF Galaxy Redshift Survey: two methods, two lines, quality flag).
- Tonry and Davis 1979, AJ 84, 1511 (redshifts by cross-correlation).
- Selsing et al. 2016, A&A 585, A87; Glikman, Helfand and White 2006, ApJ 640, 579 (quasar composites of the template).
- Lyke et al. 2020, ApJS 250, 8 (SDSS quasar catalogue DR16Q).
- Davies et al. 2026, arXiv:2603.10135; Stepney et al. 2026, arXiv:2605.06791 (redshifts of quasars from SPHEREx spectra).
- Willmer 2018, ApJS 236, 47 (colours of the Sun, for injected objects).

**Line list**

- Kramida, Ralchenko, Reader and NIST ASD Team 2024, NIST Atomic Spectra Database 5.12, doi:10.18434/T4W30F.
- Polyansky et al. 2018, MNRAS 480, 2597 (water band origins).
- Shimanouchi 1972, NSRDS-NBS 39 (vibrational frequencies; NIST Chemistry WebBook).
- Boogert, Gerakines and Whittet 2015, ARA&A 53, 541; McClure et al. 2023, Nature Astronomy 7, 431 (ices).
- van Diedenhoven et al. 2004, ApJ 611, 928; Draine and Li 2007, ApJ 657, 810 (PAH).
- Geballe et al. 2002, ApJ 564, 466; Cushing, Rayner and Vacca 2005, ApJ 623, 1115; Yamamura, Tsuji and Tanabé 2010, ApJ 722, 682 (bands in brown dwarfs).

**Subsystem documents in the repository:** [imaging engine](../imaging-engine.md), [measurement pipeline](../measurement-pipeline.md), [moving objects](../moving-objects.md), [distance and redshift](../distance-redshift.md), [data and sources](../data-sources.md), [features](../features.md).
