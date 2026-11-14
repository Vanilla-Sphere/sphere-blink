"""Checks the redshift read off SPHEREx spectra (web/redshift.js) against published redshifts.

Takes bright quasars with a spectroscopic redshift from web/data/redshift.json, asks a running engine
for the SPHEREx spectrum of each, runs the estimator on it (the same file the viewer loads, under
Node) and prints, object by object, the estimate next to the published value. Stars and nearby
galaxies go through the same way: for them the right answer is no redshift, or zero.

    uvicorn sphereblink.main:app --port 8000          # an engine of your own: never the public site
    python scripts/validate_redshift.py --keep /tmp/zcheck
    python scripts/validate_redshift.py --keep /tmp/zcheck2 --ranks 6 12 --seed 11 --no-controls

--ranks a b takes, in each of 15 redshift ranges, the quasars from the a-th to the b-th brightest in
K (default the six brightest), and 14 fainter ones (K 14.3 to 15.4) drawn with --seed. The spectra are
kept in --keep, so a second run does not ask for them again. An estimate counts as right when it is
within 2 % of the published value in (1 + z): less than one resolution element of SPHEREx.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANGES = [0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7.2]
CONTROLS = {
    "star": ["GJ 1214", "LHS 1140", "TRAPPIST-1", "Wolf 359", "GJ 436", "Luhman 16", "2MASS J04151954-0935066",
             "2MASS J00361617+1821104", "DENIS J025503.3-470049", "SIMP J013656.5+093347.3", "HD 165459", "HD 209458",
             "GSPC P330-E", "HD 180609", "HD 189733", "T Tau", "DG Tau", "NGC 6543", "CW Leo", "R Scl", "Y CVn", "chi Cyg",
             "HD 37962", "BD+17 4708", "GD 153", "TW Hya", "AB Aur", "V1057 Cyg", "gamma Cas", "MWC 349", "eta Car",
             "WR 140", "P Cyg", "SS 433", "Mira"],
    "galaxy": ["M 82", "M 77", "NGC 2336", "Mrk 231", "NGC 4151", "Arp 220", "NGC 6240", "NGC 253", "Mrk 421", "NGC 4051",
               "M 87", "NGC 5548", "NGC 7469", "Mrk 509", "I Zw 1", "NGC 1365", "M 51", "NGC 3379", "Fairall 9",
               "ESO 141-55", "Mrk 335", "3C 120", "NGC 3783", "IC 4329A"],
}


def sample(first: int, last: int, seed: int) -> list[dict]:
    rows = json.loads((ROOT / "web" / "data" / "redshift.json").read_text())["rows"]
    quasars = [r for r in rows if r[3] == "q" and r[4] is not None]
    picked = []
    for lo, hi in zip(RANGES[:-1], RANGES[1:]):
        picked += sorted((r for r in quasars if lo <= r[2] < hi), key=lambda r: r[4])[first:last]
    random.seed(seed)
    picked += random.sample([r for r in quasars if 14.3 <= r[4] <= 15.4 and 0.3 < r[2] < 5], 14)
    seen, out = set(), []
    for ra, dec, z, _, k, name, _ in picked:
        if name not in seen:
            seen.add(name)
            out.append({"name": name, "query": f"ra={ra}&dec={dec}", "z": z, "K": k, "kind": "quasar"})
    return out


def spectrum(engine: str, query: str) -> list | None:
    """The points of /api/spectrum, waiting until the engine has read every exposure."""
    for _ in range(10):
        try:
            with urllib.request.urlopen(f"{engine}/api/spectrum?{query}&fov=8&wait=110", timeout=130) as r:
                d = json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 422):
                return None
            time.sleep(3)
            continue
        except OSError:
            time.sleep(3)
            continue
        if d.get("job", {}).get("status") in ("ready", "error"):
            return d.get("points") or None
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine", default="http://localhost:8000")
    ap.add_argument("--keep", type=Path, required=True, help="folder where the spectra are kept")
    ap.add_argument("--ranks", type=int, nargs=2, default=(0, 6), metavar=("FROM", "TO"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-controls", action="store_true", help="quasars only")
    args = ap.parse_args()
    if "vanillasphere.com" in args.engine:
        sys.exit("Run this against an engine of your own, not the public site: it reads some 150 spectra.")
    args.keep.mkdir(parents=True, exist_ok=True)

    objects = sample(args.ranks[0], args.ranks[1], args.seed)
    if not args.no_controls:
        objects += [{"name": n, "query": "target=" + urllib.parse.quote(n), "kind": kind} for kind, names in CONTROLS.items() for n in names]
    spectra = []
    for o in objects:
        path = args.keep / (re.sub(r"[^a-z0-9]+", "-", o["name"].lower()).strip("-") + ".json")
        if not path.exists():
            points = spectrum(args.engine, o["query"])
            if points is None:
                print(f"{o['name']}: no spectrum", file=sys.stderr)
                continue
            path.write_text(json.dumps({**o, "points": points}))
        spectra.append({"name": o["name"], "points": json.loads(path.read_text())["points"]})

    run = subprocess.run(["node", str(ROOT / "tests" / "redshift_runner.js")], input=json.dumps(spectra), capture_output=True, text=True, timeout=600)
    if run.returncode:
        sys.exit(run.stderr)
    found = json.loads(run.stdout)

    tally = {kind: {"n": 0, "given": 0, "right": 0, "wrong": 0, "q4": 0, "off": [], "none": {}} for kind in ("quasar", "star", "galaxy")}
    for o in sorted(objects, key=lambda o: (o["kind"] != "quasar", o["kind"], o.get("z", 0))):
        e = found.get(o["name"])
        if e is None:
            continue
        t = tally[o["kind"]]
        t["n"] += 1
        verdict = ""
        if e["z"] is None:
            t["none"][e["reason"]] = t["none"].get(e["reason"], 0) + 1
        else:
            t["given"] += 1
            t["q4"] += e["quality"] == 4
            if o["kind"] == "quasar":
                t["off"].append(abs(e["z"] - o["z"]) / (1 + o["z"]))
                right = t["off"][-1] < 0.02
            else:   # a star or a galaxy next door: the lines can only be at rest
                right = abs(e["z"]) < 0.05
            t["right" if right else "wrong"] += 1
            verdict = "right" if right else "WRONG"
        known = f"z = {o['z']:<7} K = {o['K']:<5}" if "z" in o else o["kind"].ljust(21)
        given = f"z = {e['z']:.3f} ± {e['zErr']:.3f}  quality {e['quality']}" if e["z"] is not None else f"none ({e['reason']})"
        lines = ", ".join(f"{line['name']} {line['wave']:.2f}" for line in e["lines"]) if e["z"] is not None else ""
        print(f"{o['name'][:28]:<29}{known}  {given:<34}{verdict:<6} {lines}")
    print()
    for kind, t in tally.items():
        if not t["n"]:
            continue
        print(f"{kind}: {t['n']} spectra, {t['given']} given a redshift ({t['q4']} of quality 4), {t['right']} right, {t['wrong']} wrong")
        if t["off"]:
            off = sorted(t["off"])
            print(f"  off the published value, in (1 + z): median {100 * off[len(off) // 2]:.2f} %, largest {100 * off[-1]:.2f} %")
        if t["none"]:
            print("  none given: " + ", ".join(f"{n} {why}" for why, n in sorted(t["none"].items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
