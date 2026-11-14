"""Build web/data/redshift.json, the galaxies and quasars the sky map can show by redshift.

Source: SIMBAD (CDS, Strasbourg), table ``basic`` joined to ``allfluxes``,
through TAP. Only redshifts that SIMBAD marks as spectroscopic are taken, and
none of quality E. Nothing is measured here.

    python scripts/build_redshift.py

What goes in:
    z >= 5       every galaxy and quasar SIMBAD lists (a few thousand)
    below z = 5  per redshift range, the PER_BIN brightest in the K band
                 (2.2 um): the ones SPHEREx has the best chance to record

Left out above z = 5, as evident misfits of survey pipelines: galaxies whose
redshift comes from an SDSS data release paper (the SDSS spectra that are
really that far are quasars), and anything brighter than K = 15 (the
brightest quasar known there, SDSS J0100+2802, has K = 15.2). What remains
is what SIMBAD lists; a wrong redshift can still be among it, which is why
every object carries the paper its value comes from.

Output layout (angles in degrees, J2000):
    rows  [[ra, dec, z, kind, K, name, ref], ...] sorted by z; kind is "g"
          (galaxy) or "q" (quasar or blazar); K is the K magnitude or null;
          ref is an index into refs
    refs  the bibcodes of the papers the redshifts come from
    bins  the redshift ranges and how many objects each holds
"""
import csv
import io
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sphereblink.distance import kind_of  # noqa: E402

TAP = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync", "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
BINS = [(0.002, 0.02), (0.02, 0.1), (0.1, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0)]
PER_BIN = 700
COLUMNS = "b.main_id, b.ra, b.dec, b.otype, b.rvz_redshift, b.rvz_bibcode, f.K"
WHERE = "b.rvz_nature LIKE 's%' AND b.rvz_qual <> 'E' AND b.ra IS NOT NULL"
BRIGHT = ("SELECT TOP {top} " + COLUMNS + " FROM basic AS b JOIN allfluxes AS f ON f.oidref = b.oid "
          "WHERE b.rvz_redshift >= {lo} AND b.rvz_redshift < {hi} AND " + WHERE + " AND f.K IS NOT NULL ORDER BY K")
FAR = ("SELECT TOP 20000 " + COLUMNS + " FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid "
       "WHERE b.rvz_redshift >= 5 AND b.rvz_redshift < 20 AND " + WHERE)


SDSS_RELEASES = {"2009ApJS..182..543A", "2011ApJS..193...29A", "2012ApJS..203...21A", "2014ApJS..211...17A", "2015ApJS..219...12A",
                 "2017ApJS..233...25A", "2018ApJS..235...42A", "2019ApJS..240...23A", "2020ApJS..249....3A", "2022ApJS..259...35A"}
FAR_K_LIMIT = 15.0


def believable(row: list) -> bool:
    """False for the evident pipeline misfits above z = 5 (see the top of this file)."""
    return not ((row[3] == "g" and row[6] in SDSS_RELEASES) or (row[4] is not None and row[4] < FAR_K_LIMIT))


def tap(adql: str) -> list[dict]:
    for attempt in range(3):
        for url in TAP:
            try:
                r = httpx.get(url, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql}, timeout=600)
                r.raise_for_status()
                if r.text.lstrip().startswith("<"):
                    raise ValueError("not a table")
                return list(csv.DictReader(io.StringIO(r.text)))
            except Exception as exc:
                print(f"  {httpx.URL(url).host}: {type(exc).__name__}", flush=True)
        time.sleep(10)
    raise SystemExit("SIMBAD did not answer")


def rows_of(found: list[dict]) -> list[list]:
    out = []
    for r in found:
        kind = kind_of(r["otype"])
        if kind not in ("galaxy", "quasar"):
            continue
        k = r.get("K") or ""
        out.append([round(float(r["ra"]), 4), round(float(r["dec"]), 4), round(float(r["rvz_redshift"]), 4),
                    "q" if kind == "quasar" else "g", round(float(k), 1) if k else None,
                    " ".join(r["main_id"].split()).removeprefix("NAME "), r["rvz_bibcode"] or ""])
    return out


def main() -> None:
    rows, bins = [], []
    for lo, hi in BINS:
        t0 = time.time()
        # stars and other things with a velocity are in the answer too: ask for more than is kept
        found = rows_of(tap(BRIGHT.format(top=PER_BIN * 5, lo=lo, hi=hi)))[:PER_BIN]
        print(f"z {lo}-{hi}: {len(found)} kept, faintest K {found[-1][4] if found else '-'} ({time.time() - t0:.0f} s)", flush=True)
        rows += found
        bins.append({"from": lo, "to": hi, "count": len(found), "selection": f"the {PER_BIN} brightest in K"})
    listed = rows_of(tap(FAR))
    far = [r for r in listed if believable(r)]
    print(f"z >= 5: {len(far)} kept of {len(listed)} listed", flush=True)
    rows += far
    bins.append({"from": 5.0, "to": None, "count": len(far), "selection": "all, less evident pipeline misfits"})
    seen, unique = set(), []
    for r in sorted(rows, key=lambda r: r[2]):
        if r[5] not in seen:
            seen.add(r[5])
            unique.append(r)
    refs = sorted({r[6] for r in unique})
    index = {b: i for i, b in enumerate(refs)}
    for r in unique:
        r[6] = index[r[6]]
    out = {
        "source": "SIMBAD (CDS, Strasbourg): spectroscopic redshifts of galaxies and quasars, quality A to D",
        "made": date.today().isoformat(),
        "columns": ["ra", "dec", "z", "kind", "K", "name", "ref"],
        "bins": bins, "refs": refs, "rows": unique,
    }
    path = Path(__file__).resolve().parent.parent / "web" / "data" / "redshift.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(unique)} objects, {len(refs)} papers, {path.stat().st_size / 1024:.0f} KB -> {path}")


if __name__ == "__main__":
    main()
