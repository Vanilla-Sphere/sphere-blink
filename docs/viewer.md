# Viewer

The web client in `web/`. It is plain HTML, CSS and JavaScript with no
framework, no dependencies and no build step; the API service serves the folder
as static files.

| File | Size | Role |
| --- | --- | --- |
| `index.html` | 206 lines | Page structure for both screens and both modes; all fixed text |
| `style.css` | 418 lines | Design tokens, layout, components, responsive rules |
| `app.js` | about 1000 lines | State, URL handling, loading, image arithmetic, drawing, plots, routing |
| `starmap.js` | 327 lines | The sky map (`SkyMap`): projection, drawing, pan and zoom, picking |
| `i18n.js` | 107 lines | Text that scripts put on the page (`STR.en`) and the `tr()` formatter |
| `hints.js` | 74 lines | One-line notes on controls, shown on hover and keyboard focus |
| `lore.js` | 130 lines | What the sky map says about each of the 88 constellations and 31 famous stars under the pointer (`LORE`) |
| `data/sky.json` | - | Compact star chart: stars, names, constellations, Milky Way, boundary grid |
| `fonts/` | - | Hanken Grotesk and Geist Mono, self-hosted |

Scripts are loaded in the order `i18n.js`, `lore.js`, `hints.js`, `starmap.js`, `app.js`.
Asset URLs carry a version query (`?v=21`) that is raised when a file changes,
and `index.html` is served with `Cache-Control: no-cache`. A file asked for
with a version query is served as `public, max-age=604800, immutable`, so a
changed file must get a new version number; fonts and images are kept for a
day; a file asked for without a version is revalidated on every load.

## Screens and modes

The page has two screens and two modes, expressed as attributes on the root
element so that CSS alone decides what is visible:

```html
<html data-ui="simple|adv" data-screen="map|view">
```

| | `data-screen="map"` | `data-screen="view"` |
| --- | --- | --- |
| Content | Whole-sky map, featured targets, zoom buttons | Image stage, transport, and the panels of the current mode |
| Entered by | Opening `/` without a position; "Sky map" button; browser Back | Clicking the map; searching; opening a link with a position |

Elements that belong to one mode carry the class `adv` or `simple`.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/diagrams/viewer-screens-dark.png">
  <img alt="State diagram showing the sky map screen and the view screen, how the address chooses between them, and the Simple and Advanced modes inside the view screen." src="img/diagrams/viewer-screens.png">
</picture>

*Figure: Screens and modes of the viewer. Source: [`diagrams/viewer-screens.html`](diagrams/viewer-screens.html).*

### Layout in Advanced mode

Three columns under a fixed top bar:

| Region | Content |
| --- | --- |
| Top bar | Brand, "Sky map" button, search box, Field menu, "Show sky", Simple/Advanced switch, API and Source links, credits note |
| Left rail | Featured targets; Display panel: Bands, One frame per, Image math, Contrast, Overlays |
| Stage | Target name and coordinates; the image; progress bar; a status strip with date, channels, wavelength range, exposure count and pointer coordinates; transport; timeline |
| Right column | Spectrum or light curve; "This frame"; "Measured in this field"; "Position of the crosshair" with cross-links; "Link or embed this view" |

### Layout in Simple mode

The image with play, slider and date, beside one panel: the object's name and
kind, a short description, Share and Download GIF, and a link to Advanced. The
left rail and the analysis column are hidden.

## State

A single object `S` in `app.js` holds the state of the page:

| Group | Fields |
| --- | --- |
| Target and view | `target`, `ra`, `dec`, `fov`, `levels`, `t` (resolved target), `grid` |
| Display | `det`, `group`, `mode`, `gain`, `fps`, `i` (epoch on screen), `ref` (reference epoch), `playing` |
| Data | `cube`, `meta`, `npix`, `planes`, `limits`, `median`, `stack`, `spectrum`, `catalogs`, `tracks` |
| Progressive field | `field` (the server's report), `stage` (the stage on screen) |
| Interface | `ui`, `screen`, `plotmode`, `keep` (Advanced settings saved while in Simple) |
| Control | `loadId` (incremented on every load so that answers to superseded requests are discarded) |

## URL state

Everything needed to reproduce a view is in the address. `readUrl` applies it
at start; `writeUrl` keeps it current (`replaceState` for display changes,
`pushState` when a new view is opened from the map).

| Parameter | Values | Default | Meaning |
| --- | --- | --- | --- |
| `target` | text | - | Object name or challenge-target key |
| `ra`, `dec` | degrees | - | Position; used instead of `target` for map clicks and recentring |
| `fov` | 1-30 | `8` | Field of view in arcminutes |
| `levels` | 0-3 | `0` | Progressive field with this many rings (replaces `fov`) |
| `det` | `rgb`, `all`, list | `rgb` | Bands |
| `group` | `day`, `visit`, `exposure` | `day` | Frame grouping |
| `mode` | `blink`, `diffmed`, `diffref`, `stack` | `blink` | Image arithmetic |
| `gaia`, `catwise` | `1` | off | Catalogue overlays |
| `tracks` | `0` | on | Hide the measured-tracks overlay |
| `ui` | `simple`, `adv` | see below | Interface mode |

Only non-default display values are written. The interface mode is resolved in
this order: the `ui` parameter; Advanced if the link carries any display
parameter (a link made before modes existed); the last mode used in this
browser (`localStorage` key `vs.ui`); Simple.

Browser Back and Forward are handled by `route`: an address without a position
shows the map, the address of the current view shows it again without
reloading, and any other address loads that view.

## Loading a view

`load(next)` is the single entry point for opening or changing a view.

1. Call `/api/view` (or `/api/tiles` for a progressive field). If the name does
   not resolve, show the error under the search box and leave the current view
   untouched.
2. Reset the data state, show the target, switch to the view screen, write the URL.
3. Poll `/api/cube` every 0.9-1.3 s until the job is ready. Each answer replaces
   the cube, rebuilds the timeline and redraws, so the movie grows as exposures
   arrive. The epoch on screen is preserved across refreshes by its label.
4. When ready, request the spectrum, the selected catalogues and the measured tracks.

For a progressive field, `loadField` polls `/api/tiles` and fetches the cube of
the widest finished stage; stages wider than 300 pixels are requested with
`bin=2`.

Changing bands or grouping calls `reloadCube`, which fetches the cube again for
the same view; the frames are already cached on the server, so only the
stacking is repeated. A sequence number discards answers that a newer request
has overtaken.

## Rendering

### Decoding

The cube is decoded once into a `Float32Array` (see
[binary cube format](api-reference.md#binary-cube-format)); "no data" becomes NaN.

### Image arithmetic

Computed on the decoded cube, plane by plane:

| Mode | Computation |
| --- | --- |
| Blink | The epoch as decoded |
| - Median | Epoch minus the per-pixel median over all epochs (cached after first use) |
| - Reference | Epoch minus the chosen reference epoch |
| Sum all | Per-pixel mean over all epochs multiplied by the square root of their number |

### Stretch

`limits()` samples about 20 000 pixels per plane from each of up to about 30
epochs and derives one pair of limits per plane for the whole movie: the 2nd and 99.7th
percentiles (colour planes start one robust sigma above the median); for
difference modes, plus and minus the 99.7th percentile of absolute values. The
contrast slider scales the upper limit by a power of ten between 10^-1.5 and
10^1.5.

`draw()` applies the same curves as the server's renderer: `asinh(10 t)` for
grey images, the asinh of the mean of three channels with preserved ratios for
colour, and a symmetric `asinh(4 t)` for differences. The image is written to an
off-screen canvas at native size and scaled to the display canvas, with
smoothing in Simple mode and true pixels in Advanced unless "Smooth pixels" is
ticked.

### Overlays

Drawn on the image canvas after every frame by `drawOverlays`:

| Overlay | Drawing |
| --- | --- |
| Cell grid (progressive fields) | Cell outlines and a block map filling in ring by ring |
| Crosshair | On the target; in a progressive field it stays on the target while the mosaic is centred on the seed cell |
| Gaia DR3 / CatWISE | Circle or square at the position propagated to the date of the frame; fast, significant movers highlighted with a path and a label |
| Measured tracks | Every measured position as a dot, the fitted track as a line, a diamond on positions measured in the frame on screen, the object's name |
| Scale bar and compass | 1, 2 or 5 arcmin bar depending on the field; N up, E left |

In Advanced both catalogues are fetched as soon as a view opens (`loadCatalogs`),
so ticking an overlay shows it without a wait; Simple fetches none.

Marks and text keep a constant size in CSS pixels on any display density and
carry a thin dark edge so they remain legible over bright pixels. Canvas colours
are read from the CSS custom properties, so the canvas follows the design tokens.

### Plots

`drawPlot` draws the spectrum or the light curve on a canvas without a charting
library: axes with rounded tick steps, points coloured by sky pass (spectrum) or
by detector (light curve), hollow points for flagged apertures, diamonds for
halo-measured fluxes, hollow diamonds above the calibrated range, and a legend.
The vertical axis reads in mJy and switches to Jy when the 98th percentile of
the fluxes exceeds 1 Jy.

## Sky map

`SkyMap` in `starmap.js` exposes `init`, `show`, `redraw`, `zoom`, `home`,
`lookAt`, `setMark`, `setPins`, `highlight`, `idle` and `constellation`, and
takes the callbacks `onPick`, `onHover`, `onView` and `onReady`. `onHover` reports
the sky position, the constellation, a target pin and a named star within 9 px
of the pointer; `app.js` turns that into the map tag (pin, then star, then
constellation, with the text from `lore.js`), and the map brightens the figure
of the constellation under the pointer.

- **Projection.** Hammer equal-area about a movable centre. Positions are
  rotated as unit vectors so the centre can be any point of the sky.
- **Data.** `data/sky.json` holds stars as a flat integer array (RA, Dec,
  magnitude, colour index), star names, constellation lines and label
  positions, the Milky Way as a dot grid with five brightness levels, and a
  run-length-encoded 1 degree grid naming the constellation of each cell. It is
  built by `scripts/build_sky.py` from d3-celestial data and Astropy's
  constellation boundaries.
- **Level of detail.** Fainter stars appear as the map is zoomed.
- **Input.** Pointer events for drag, pinch and click; wheel zoom about the
  pointer so the sky under it stays under it; keyboard pan, zoom and select.
- **Drawing** is batched with `requestAnimationFrame` and only runs while the
  map screen is shown.

## Text and notes

- Fixed text is written in `index.html`. Text produced by scripts comes from
  `STR.en` through `tr(key, vars)`, which fills `{name}` placeholders.
- The interface is in English.
- **Explanations stay out of the way.** The screen carries minimal text. Longer
  explanations are behind small "i" buttons (`.tip`, `.tipbtn`, `.tipbody`) that
  open on hover, focus or tap and close on Escape.
- **Every control has a one-line note.** A control opts in with
  `data-hint="key"` (text in `STR.en`) or `data-hint-text`. `hints.js` shows the
  note after a short delay on hover or keyboard focus, never on touch. A
  container with `data-hint-side="right"` or `"top"` positions the note so that
  it does not cover the next control. Native `title` tooltips are not used.

## Design system

The visual language is the "Rim" theme of Hallmark in its night colourway, laid
out by the tool-interface rules of Impeccable (both MIT-licensed design
references).

| Token group | Definition |
| --- | --- |
| Surfaces | Near-black "paper" with a violet cast in three steps (`--color-paper`, `-2`, `-3`) and a lift tone |
| Text | Warm off-white "ink" in two steps and two muted steps |
| Rules | Hairlines in two strengths; zero corner radius throughout |
| Signal | Nebula violet `oklch(72% 0.165 300)` for actions and current state only; pale cyan as the second accent |
| Logo | Its own two colours, independent of the accent: an orange V (`--brand-v`) holding a vanilla-cream sphere (`--brand-sphere`) |
| Data series | Six detector colours in wavelength order (`--d1` ... `--d6`), three channel colours, one colour for measured tracks, one for "no data" |
| Type | Hanken Grotesk for interface text; Geist Mono with tabular figures for every measured value |
| Motion | One easing curve, 140 ms; disabled under `prefers-reduced-motion` |

Rules applied throughout:

- Violet marks what can be acted on or what is current, nothing else.
- Every measurement is set in the monospaced face; every time is labelled UTC.
- Pixels without data have their own colour so they are never mistaken for sky.
- Colours are defined in OKLCH and referenced only through custom properties.

## Responsive behaviour and accessibility

- **Breakpoints** at 1180, 1000, 900, 760 and 480 px. From 1180 px the three
  columns sit side by side in a fixed shell and each scrolls on its own. Below
  that the stage stays in view beside one column holding the settings and the
  analysis. Below 1000 px the search box moves to its own row; below 900 px
  Simple mode becomes a single centred column. Below 760 px the whole layout is
  one scrolling column with controls at least 40 px high, and below 480 px the
  brand collapses to its mark.
- **Image size** is derived from the viewport so the image, transport and
  timeline fit one screen without scrolling on a desktop display.
- **Touch.** Enlarged hit areas for the "i" buttons under `pointer: coarse`;
  pinch zoom on the map; the system share sheet for Share; hover notes are
  suppressed.
- **Keyboard.** All controls are reachable by Tab with a visible focus ring.
  Space plays and pauses, arrow keys step frames; on the map, arrow keys pan,
  `+` and `-` zoom and Enter opens the centre.
- **Semantics.** Landmarks (`header`, `main`, `aside`, `nav`), labelled groups
  for segmented controls, `aria-pressed` on mode buttons, `role="alert"` for
  search errors, `role="status"` for progress text, `role="tooltip"` for notes,
  and descriptive labels on canvases and icon buttons.
- **Document title** follows the target ("Barnard's Star - Vanilla Sphere").

## Conventions for changes

- New interface elements follow the tokens above and go in the correct mode
  (class `adv` or `simple`).
- Every new control gets a hover note through `data-hint`.
- Script-made text goes in `STR.en`; fixed text in `index.html`.
- Raise the `?v=` version on the asset references in `index.html` when a script
  or the stylesheet changes.
- Several workstreams edit `web/app.js`; rebase on `main` before changing it.
