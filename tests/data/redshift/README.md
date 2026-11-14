# Spectra for the tests of the redshift estimate

Points of `/api/spectrum` for 14 quasars with a published spectroscopic
redshift (`z`, as listed by SIMBAD), taken on 2026-11-14 with a local engine
and kept in a compact form: one row per exposure, only the direct measurements
on unflagged pixels (`columns` names the fields). They are measurements on
NASA SPHEREx Level 2 spectral images (NASA/JPL-Caltech, served by NASA/IPAC
IRSA), made as described in `docs/imaging-engine.md`.

Eight show emission lines and must give their redshift back; four are blazars
(OJ 287, Ton 599, 3C 279, PG 1553+113), whose light changes from day to day
and shows no lines, and one shows a single line: those five must give none.
`tests/test_redshift.py` checks that `web/redshift.js` does so.

One more, SDSS J152846.73+363751.5, is the case of a wrong catalogue value:
SIMBAD lists it at z = 4.3436 (`z_simbad`, from the automatic SDSS pipeline),
the SDSS quasar catalogue DR16Q and NED at 2.4129 (`z`), and the lines of its
SPHEREx spectrum stand where 2.41 puts them. `tests/test_insight.py` checks
that the reading of the spectrum then uses the measured redshift and says why.
