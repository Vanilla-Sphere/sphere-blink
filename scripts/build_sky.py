"""Build web/data/sky.json, the compact star chart behind the sky map.

Input: a folder holding these files from d3-celestial (Olaf Frohn, BSD
3-Clause, https://github.com/ofrohn/d3-celestial/tree/master/data):
stars.6.json, starnames.json, constellations.json, constellations.lines.json
and mw.json.

    python scripts/build_sky.py path/to/d3-celestial/data

Output layout (angles in degrees, J2000):
    stars  flat [ra*1000, dec*1000, mag*10, bv*10, ...], brightest first
           (positions to 0.001 degree: a click on a star opens the star)
    ids    one designation per star, in the order of stars: Bayer letter,
           Flamsteed number or variable-star name with the constellation
           ("τ Phe", "61 Cyg", "CG And"), or "" when the star has none
    names  [[ra, dec, mag, name, index into stars], ...] proper names of
           all stars that have one; the map labels those to magnitude 3
    cons   [{id, la, at: [ra, dec], lines: [[[ra, dec], ...], ...]}]
    mw     flat [ra*10, dec*10, level, ...], a dot grid aligned with the
           galactic plane; level 1 (faint edge) to 5 (bright core)
    grid   run-length list [index into cons ids, run, ...] over 1 degree
           cells, rows from dec -90 up, RA from 0, naming the constellation
           of each cell (IAU boundaries through astropy)
"""
import json
import sys
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy.coordinates import Galactic, SkyCoord, get_constellation

def inside(lon, lat, ring):
    """Even-odd test of points against one ring given in degrees."""
    ring = np.asarray(ring, float)
    rl = np.degrees(np.unwrap(np.radians(ring[:, 0])))
    rb = ring[:, 1]
    x1, y1, x2, y2 = rl[:-1], rb[:-1], rl[1:], rb[1:]
    hit = np.zeros(lon.shape, bool)
    for shift in (-360.0, 0.0, 360.0):
        px = (lon + shift)[:, None]
        py = lat[:, None]
        cross = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        hit ^= (np.sum(cross & (px < xi), axis=1) % 2).astype(bool)
    return hit


def main(src):
    src = Path(src)
    load = lambda n: json.loads((src / n).read_text(encoding="utf-8"))

    stars = sorted(load("stars.6.json")["features"], key=lambda f: f["properties"]["mag"])
    names_src = load("starnames.json")
    flat, desig, names = [], [], []
    for i, f in enumerate(stars):
        lon, lat = f["geometry"]["coordinates"]
        bv = f["properties"].get("bv") or "0.6"
        flat += [round((lon % 360) * 1000) % 360000, round(lat * 1000), round(f["properties"]["mag"] * 10), round(float(bv) * 10)]
        about = names_src.get(str(f["id"]), {})
        letter = about.get("bayer") or about.get("flam") or about.get("var") or ""
        desig.append(f"{letter} {about['c']}" if letter and about.get("c") else "")
        if about.get("name"):
            names.append([round(lon % 360, 2), round(lat, 2), f["properties"]["mag"], about["name"], i])

    lines = {f["id"]: f["geometry"]["coordinates"] for f in load("constellations.lines.json")["features"]}
    cons, ids = [], []
    for f in load("constellations.json")["features"]:
        cid = f["id"]
        lon, lat = f["geometry"]["coordinates"]
        entry = {"id": cid, "la": f["properties"]["name"], "at": [round(lon % 360, 1), round(lat, 1)],
                 "lines": [[[round(p[0] % 360, 3), round(p[1], 3)] for p in seg] for seg in lines.get(cid, [])]}
        cons.append(entry)
        ids.append(cid)
    first = {}
    for i, cid in enumerate(ids):
        first.setdefault(cid[:3], i)

    # Milky Way: a dot grid laid along the galactic plane.
    step = 1.25
    pts = []
    for b in np.arange(-45, 45 + step, step):
        n = max(1, int(round(360 * np.cos(np.radians(b)) / step)))
        off = 0.5 if int(round(b / step)) % 2 else 0.0
        for k in range(n):
            pts.append(((k + off) * 360.0 / n, b))
    pts = np.array(pts)
    eq = SkyCoord(l=pts[:, 0] * u.deg, b=pts[:, 1] * u.deg, frame=Galactic).icrs
    ra, dec = eq.ra.deg, eq.dec.deg
    lon = np.where(ra > 180, ra - 360, ra)
    level = np.zeros(len(pts), int)
    for f in load("mw.json")["features"]:
        lv = int(f["id"][2:])
        hit = np.zeros(len(pts), bool)
        for poly in f["geometry"]["coordinates"]:
            for ring in poly:
                hit ^= inside(lon, dec, ring)
        level = np.where(hit, np.maximum(level, lv), level)
    mw = []
    for r, d, lv in zip(ra, dec, level):
        if lv:
            mw += [round(r * 10), round(d * 10), int(lv)]

    # Constellation of every 1 degree cell.
    rr, dd = np.meshgrid(np.arange(0.5, 360), np.arange(-89.5, 90))
    short = get_constellation(SkyCoord(rr.ravel() * u.deg, dd.ravel() * u.deg), short_name=True)
    idx = np.array([first[s] for s in short])
    grid, start = [], 0
    for i in range(1, len(idx) + 1):
        if i == len(idx) or idx[i] != idx[start]:
            grid += [int(idx[start]), i - start]
            start = i

    out = {"stars": flat, "ids": desig, "names": names, "cons": cons, "mw": mw, "grid": grid}
    dest = Path(__file__).resolve().parent.parent / "web" / "data" / "sky.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{dest}: {dest.stat().st_size / 1024:.0f} kB, {len(stars)} stars, {len(names)} names, "
          f"{len(cons)} constellations, {len(mw) // 3} Milky Way dots, {len(grid) // 2} grid runs")


if __name__ == "__main__":
    main(sys.argv[1])
