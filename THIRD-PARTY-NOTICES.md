# Third-party notices

Vanilla Sphere's own source code is under the GNU Affero General Public
License, version 3 (see [`LICENSE`](LICENSE) and the README). The items below
are in this repository or are used by it, belong to others, and keep their own
terms. Credits for the data the service shows are in
[docs/data-sources.md](docs/data-sources.md#credits-and-licences).

## Files in this repository

| Files | From | Licence or terms |
| --- | --- | --- |
| `web/fonts/HankenGrotesk.woff2` | Hanken Grotesk, Copyright 2021 The Hanken Grotesk Project Authors (https://github.com/marcologous/hanken-grotesk) | SIL Open Font License 1.1, full text in `web/fonts/OFL-HankenGrotesk.txt` |
| `web/fonts/GeistMono.woff2` | Geist Mono, Copyright 2024 The Geist Project Authors (https://github.com/vercel/geist-font.git) | SIL Open Font License 1.1, full text in `web/fonts/OFL-GeistMono.txt` |
| `web/data/sky.json` | Reduced by `scripts/build_sky.py` from the data files of d3-celestial, Copyright (c) 2015, Olaf Frohn (https://github.com/ofrohn/d3-celestial) | BSD 3-Clause, full text in `web/data/LICENSE-d3-celestial.txt` |
| `web/data/redshift.json` | Extract of the SIMBAD database, CDS, Strasbourg, France (Wenger et al. 2000), made by `scripts/build_redshift.py`; every redshift carries the bibcode of its paper | The terms of CDS for its datasets (https://cds.unistra.fr/legals/) |
| `web/data/quasar-template.json` | Built by `scripts/build_quasar_template.py` from Selsing et al. 2016, A&A 585, A87 and Glikman, Helfand and White 2006, ApJ 640, 579, read from VizieR (CDS) | The terms of CDS for its datasets; cite the two papers |
| Reference wavelengths in `web/linelist.js` | NIST Atomic Spectra Database, version 5.12 (Kramida, Ralchenko, Reader and NIST ASD Team 2024, doi:10.18434/T4W30F), and the papers named in that file | NIST Standard Reference Data, copyright U.S. Department of Commerce; NIST is acknowledged as the source. Published literature for the rest |
| `tests/data/` | Answers of the catalogue services (SIMBAD, NED, the ESA Gaia archive, IRSA) recorded for the tests, and SPHEREx spectra measured by this service | The terms of each source, see docs/data-sources.md |

These files are data and fonts, not part of the program; the AGPL does not
apply to them.

## Design references

The look of the viewer follows the "Rim" theme of Hallmark
(https://github.com/nutlope/hallmark) and the tool-interface rules of
Impeccable (https://github.com/pbakaus/impeccable); the diagrams in `docs/`
follow the diagram-design skill
(https://github.com/cathrynlavery/diagram-design). Their licences are in
those repositories.

The colours and surfaces of `web/style.css` are taken from Hallmark's "Rim"
theme, so its notice is repeated here:

```
MIT License

Copyright (c) 2026 Hallmark contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Libraries

Installed from `requirements.txt` when the service is built; none of them is
copied into this repository.

| Library | Licence |
| --- | --- |
| Astropy, NumPy, SciPy, httpx, fsspec, Starlette, uvicorn | BSD 3-Clause |
| FastAPI, pydantic, anyio | MIT |
| Pillow | MIT-CMU |
| aiohttp | Apache-2.0 and MIT |
| psycopg | LGPL-3.0-only |

The interactive API pages (`/docs`, `/redoc`) are FastAPI's and load Swagger UI
and ReDoc from jsDelivr.

## Name and logo

The name "Vanilla Sphere" and its logo are not licensed under the AGPL
(section 7(e)). A modified version that is offered to others should carry its
own name and logo, and may say that it is based on Vanilla Sphere.
