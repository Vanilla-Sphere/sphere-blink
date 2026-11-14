# Spectra for the tests of the preliminary reading

Points of `/api/spectrum` for 14 positions, taken from the production service
on 2026-11-14 and kept in a compact form (one row per exposure; `columns`
names the fields). They are measurements on NASA SPHEREx Level 2 spectral
images (NASA/JPL-Caltech, served by NASA/IPAC IRSA), made as described in
`docs/imaging-engine.md`.

`tests/test_insight.py` reads them to check that `web/insight.js` gives each
curve the reading its kind of object should get, and no more than that.
