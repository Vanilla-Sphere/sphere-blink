"""Measure how complete the blind search for moving objects is.

Fake moving objects go into the real exposures of a field, the search runs
on them unchanged, and what it finds is counted
(``sphereblink/mover_completeness.py`` holds the method,
``docs/mover-completeness.md`` the results). This script is the bench around
it. It reads the archive and computes for hours: run it on a workstation,
never on the server of the site.

    python scripts/mover_completeness.py fetch sparse 167.13 5.17      # every exposure of a field, to one file
    python scripts/mover_completeness.py prepare sparse                # its noise, and the search without fakes
    python scripts/mover_completeness.py run sparse --trials 200       # 200 rounds of 12 fakes each
    python scripts/mover_completeness.py shuffle sparse --trials 100   # searches with the exposure times shuffled
    python scripts/mover_completeness.py report sparse medium crowded  # the numbers, as web/data/mover-completeness.json

Everything is kept in ``--dir`` (default ``.cache/mover-completeness``); ``run``
appends one line per round to ``<field>.<search>.jsonl`` and can be stopped
and started again at any time.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
import time
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sphereblink import mover_completeness as mc  # noqa: E402
from sphereblink import movers  # noqa: E402
from sphereblink.cutout import Grid  # noqa: E402

_field: dict = {}


def load(name: str, root: Path) -> dict:
    meta = json.loads((root / f"{name}.json").read_text())
    g = meta["grid"]
    grid = Grid(ra=g["ra"], dec=g["dec"], npix=g["npix"], scale_arcsec=g["scale_arcsec"])
    out = {"name": name, "grid": grid, "frames": meta["frames"], "cube": np.load(root / f"{name}.npy")}
    if (root / f"{name}.noise.npy").exists():
        out["noise"] = np.load(root / f"{name}.noise.npy")
        out["baseline"] = pickle.loads((root / f"{name}.base.pkl").read_bytes())["tracks"]
    return out


def searcher(kind: str):
    """The search under test: ``movers`` (the one on the site), ``stack`` (shift and stack alone),
    ``both`` (the paths of the two, as the site would list them) or ``module:function``."""
    if kind == "movers":
        return lambda cube, frames, wcs, scale: movers.search(cube, frames, wcs, scale, trials=0)
    if ":" in kind:  # any other function with that input, as module:function (a variant of the search under study)
        import importlib

        module, name = kind.split(":", 1)
        return getattr(importlib.import_module(module), name)
    from sphereblink import mover_stack

    if kind == "stack":
        return lambda cube, frames, wcs, scale: mover_stack.search(cube, frames, wcs, scale)

    def both(cube, frames, wcs, scale):
        a = movers.search(cube, frames, wcs, scale, trials=0)
        b = mover_stack.search(cube, frames, wcs, scale, exclude=a["tracks"])
        return {**a, "tracks": list(a["tracks"]) + list(b["tracks"]), "stats": {**a["stats"], "stack": b.get("stats", {})}}

    return both


# ----------------------------------------------------------------------------- commands
def fetch(args) -> None:
    from sphereblink import tiles
    from sphereblink.service import Service

    seed = tiles.block(args.ra, args.dec, 0)[0]
    grid = tiles.stage_grid(seed["ra_c"], seed["dec_c"], 3)
    t0 = time.time()
    got = Service().frames_blocking(grid, timeout=1800)
    cube = np.stack([f.image for f in got]).astype(np.float32)
    frames = [{"exposure": f"{f.exposure.obs_id}:D{f.exposure.detector}", "mjd": f.exposure.mjd, "detector": f.exposure.detector,
               "wave_um": f.wave_um} for f in got]
    np.save(args.dir / f"{args.field}.npy", cube)
    (args.dir / f"{args.field}.json").write_text(json.dumps({"name": args.field, "grid": grid.to_dict(), "frames": frames}))
    print(f"{args.field}: {cube.shape[0]} exposures of {cube.shape[1]} pixels in {time.time() - t0:.0f} s")


def prepare(args) -> None:
    f = load(args.field, args.dir)
    noise = mc.noise_maps(f["cube"], f["frames"])
    np.save(args.dir / f"{args.field}.noise.npy", noise)
    out = movers.search(f["cube"], f["frames"], f["grid"].wcs, f["grid"].scale_arcsec, trials=0)
    (args.dir / f"{args.field}.base.pkl").write_bytes(pickle.dumps({"tracks": out["tracks"], "stats": out["stats"]}))
    print(f"{args.field}: {out['stats']}")


def _round(job: tuple) -> dict:
    index, kind, count, v_mag, rate, spectrum = job
    f = _field
    seed = zlib.crc32(f"{f['name']}:{index}".encode())
    rng = np.random.default_rng(seed)
    grid = f["grid"]
    fakes = mc.draw(f["frames"], grid.wcs, grid.npix, count, rng, v_mag=v_mag, rate=rate,
                    spectrum="asteroid" if spectrum == "mixed" else spectrum)
    if spectrum == "mixed":  # every second fake is a body without heat of its own
        for fake in fakes[1::2]:
            fake.spectrum = "sun"
    t0 = time.time()
    out = mc.trial(f["cube"], f["frames"], grid.wcs, grid.scale_arcsec, fakes, search=searcher(kind), seed=seed,
                   noise=f.get("noise"), baseline=f.get("baseline"))
    stats = {k: v for k, v in out["stats"].items() if isinstance(v, (int, float))}
    return {"field": f["name"], "round": index, "seed": seed, "search": kind, "version": movers.VERSION, "seconds": round(time.time() - t0, 1),
            "baseline": len(f.get("baseline") or []), "baseline_found": out["baseline_found"], "others": out["others"],
            "stats": stats, "fakes": out["fakes"]}


def run(args) -> None:
    import multiprocessing

    global _field
    _field = load(args.field, args.dir)
    path = args.dir / f"{args.field}.{args.tag or args.search}.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(line)["round"] for line in path.read_text().splitlines() if line.strip()}
    jobs = [(i, args.search, args.fakes, tuple(args.vmag), tuple(args.rate), args.spectrum)
            for i in range(args.first, args.first + args.trials) if i not in done]
    print(f"{args.field}: {len(jobs)} rounds to go ({len(done)} done), {args.procs} at a time", flush=True)
    t0 = time.time()
    with multiprocessing.get_context("fork").Pool(args.procs, initializer=os.nice, initargs=(10,)) as pool, path.open("a") as out:
        for k, row in enumerate(pool.imap_unordered(_round, jobs), 1):
            out.write(json.dumps(row) + "\n")
            out.flush()
            found = sum(r["found"] for r in row["fakes"])
            print(f"  round {row['round']}: {found} of {len(row['fakes'])} found, {len(row['others'])} other paths, "
                  f"{row['seconds']} s ({k}/{len(jobs)}, {time.time() - t0:.0f} s)", flush=True)


def shuffle(args) -> None:
    """The search of the field as it is, with the times shuffled: what chance alone finds. Two ways, as the
    search itself does it: single exposures change places (any confirmed path counts), and whole visits change
    places (a path counts when it has the evidence asked of a nameless object)."""
    f = load(args.field, args.dir)
    grid = f["grid"]
    dets, sky, _ = movers.detect(f["cube"], f["frames"], grid.wcs, grid.scale_arcsec)
    pool = movers.drop_recurring(dets)
    visits = getattr(movers, "CANDIDATE_VISITS", None)
    taken = {d.key() for t in f.get("baseline") or [] if visits and t.extra.get("visits_seen", 0) >= visits for d in t.detections}
    rest = [d for d in pool if d.key() not in taken]  # a real object keeps part of its path when visits change places
    path = args.dir / f"{args.field}.shuffle.jsonl"
    done = len(path.read_text().splitlines()) if path.exists() else 0
    with path.open("a") as out:
        for k in range(done, args.trials):
            row = {"field": args.field, "seed": 1000 + k, "version": movers.VERSION,
                   "tracks": movers.chance_tracks(pool, f["cube"], f["frames"], grid.wcs, sky, grid.scale_arcsec, trials=1, seed=1000 + k)}
            if visits:
                row["candidates"] = movers.chance_tracks(rest, f["cube"], f["frames"], grid.wcs, sky, grid.scale_arcsec, trials=1,
                                                         seed=1000 + k, visits=True)
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"  shuffle {k}: {row['tracks']:.0f} paths, {row.get('candidates', 0):.0f} candidates", flush=True)


def report(args) -> None:
    from mover_completeness_report import build  # the tables and the picture live next to this file

    build(args)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=Path(os.environ.get("MOVER_COMPLETENESS_DIR", ".cache/mover-completeness")))
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("fetch")
    s.add_argument("field"), s.add_argument("ra", type=float), s.add_argument("dec", type=float)
    s = sub.add_parser("prepare")
    s.add_argument("field")
    s = sub.add_parser("run")
    s.add_argument("field")
    s.add_argument("--trials", type=int, default=50)
    s.add_argument("--first", type=int, default=0)
    s.add_argument("--fakes", type=int, default=12, help="fakes per round")
    s.add_argument("--vmag", type=float, nargs=2, default=(15.5, 19.8))
    s.add_argument("--rate", type=float, nargs=2, default=(2.0, 400.0), help="arcsec per hour")
    s.add_argument("--spectrum", default="mixed", choices=("mixed", "asteroid", "sun", "flat"),
                   help="mixed: asteroids (sunlight and their own heat) and bodies without heat, half and half")
    s.add_argument("--search", default="movers", help="movers, stack, both, or module:function of another search")
    s.add_argument("--tag", default="", help="name of the result file when it is not the search's")
    s.add_argument("--procs", type=int, default=min(4, os.cpu_count() or 1))
    s = sub.add_parser("shuffle")
    s.add_argument("field")
    s.add_argument("--trials", type=int, default=50)
    s = sub.add_parser("report")
    s.add_argument("fields", nargs="+")
    s.add_argument("--search", default="movers", help="the search, or the tag, of the result files to read")
    s.add_argument("--out", type=Path, default=Path("web/data/mover-completeness.json"))
    s.add_argument("--plot", type=Path, default=None)
    s.add_argument("--made", default=time.strftime("%Y-%m-%d"), help="date of the measurement")
    s.add_argument("--version", type=int, default=None,
                   help="version of the search the rounds were made with, when they do not say (rounds written before version 5)")
    s.add_argument("--same-on", type=int, nargs=2, default=None, metavar=("VERSION", "ROUNDS"),
                   help="a later version of the search that gave the same on that many of the rounds, fake by fake")
    args = p.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    {"fetch": fetch, "prepare": prepare, "run": run, "shuffle": shuffle, "report": report}[args.command](args)


if __name__ == "__main__":
    main()
