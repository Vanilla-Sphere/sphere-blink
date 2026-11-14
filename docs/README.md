# Vanilla Sphere: product design documentation

Vanilla Sphere is a web application that plays every NASA SPHEREx image of any
patch of sky as a time-ordered movie, extracts a 0.75-5.0 um spectrum at the
centre of the view, and measures the position, motion and brightness of the
object found there. It was built by team Vanilla Sphere for the 2026 NASA Space
Apps Challenge, challenge "Planet X and SPHEREx".

- Live product: <https://vanillasphere.com>
- Source: <https://github.com/Vanilla-Sphere/sphere-blink>
- Interactive API explorer: <https://vanillasphere.com/docs>

This folder is the design documentation of the product. It describes the system
as it is implemented on the `main` branch; every statement about behaviour was
checked against the source code, and every performance figure states where and
when it was measured.

## Documents

| Document | Contents |
| --- | --- |
| [Product overview](overview.md) | The problem, the users, the product in one page, design principles, scope and limits |
| [Features](features.md) | Functional specification of every user-facing feature |
| [Architecture](architecture.md) | System context, components, request flows, concurrency and caching model, design decisions |
| [Imaging engine](imaging-engine.md) | How a view is produced: exposure search, FITS range reads, cutout, resampling, epochs, false colour, progressive fields |
| [Measurement pipeline](measurement-pipeline.md) | Automatic astrometry, track fitting, light curves, bright-star photometry, validation results |
| [API reference](api-reference.md) | Every HTTP endpoint: parameters, responses, status codes, binary cube format |
| [Viewer](viewer.md) | The web client: screens, Simple and Advanced modes, URL state, rendering, design system |
| [Code structure](code-structure.md) | Repository layout, module responsibilities and dependencies, tests, conventions |
| [Data and sources](data-sources.md) | Datasets, external services, data model, units, credits and licences |
| [Performance](performance.md) | Measured timings and volumes, the optimisations behind them, capacity limits |
| [Deployment and operations](deployment.md) | Hosting, configuration, release process, health checks, runbook |

## Reading paths

- **Evaluating the product:** [overview](overview.md), then [features](features.md), then the
  validation table in [measurement pipeline](measurement-pipeline.md#validation-on-the-challenge-targets).
- **Integrating with the API:** [API reference](api-reference.md), with
  [data and sources](data-sources.md#units-and-conventions) for units.
- **Working on the code:** [architecture](architecture.md), [code structure](code-structure.md),
  then the document for the subsystem being changed.
- **Operating the service:** [deployment and operations](deployment.md) and
  [performance](performance.md#capacity-and-limits).

## Terminology

| Term | Meaning |
| --- | --- |
| Exposure | One SPHEREx Level 2 spectral image: one detector, one pointing, about 115 s. Identified by `obs_id` and detector, written `2025W33_2C_0321_1_D3`. |
| Detector (band) | One of the six SPHEREx detector arrays, D1 to D6, together covering 0.75-5.0 um. |
| LVF | Linear variable filter. The wavelength a detector pixel records depends on its position on the detector. |
| View | A square, north-up image grid centred on a position, with a field of view in arcminutes. The unit of work of the imaging engine. |
| Frame | One exposure resampled onto the grid of a view. |
| Epoch | One displayed image: a single frame, or the frames of one UTC day or one sky pass combined. |
| Sky pass (visit) | A period of days to weeks during which SPHEREx observes a position; passes recur about every six months. |
| Cell | One 0.12 degree square of the fixed ICRS grid `icrs012-v1`. |
| Progressive field | A block of cells loaded outward from a seed cell, ring by ring. "Progressive N = 3" is 7 x 7 cells. |
| Detection | The measured position (and flux) of an object in one exposure or one stack. |
| Track | The straight-line motion fitted through the detections of an object. |
