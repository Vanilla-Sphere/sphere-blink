"""Measure the shift and stack search on fake distant bodies.

The fakes of ``scripts/mover_completeness.py`` move in a straight line at 2
arcsec per hour or more, which is fair to the search that links detections.
The shift and stack search (``sphereblink/mover_stack.py``) looks for bodies
beyond Neptune, and for those a fair fake is another one: its path is mostly
Earth's own motion seen from the body, it bends within a pass, and it is in
every frame of the pass. This script makes such fakes, runs the two searches
as the site does (the linker first, shift and stack after it with the
linker's tracks kept out) and counts what comes back
(``docs/mover-completeness.md`` holds the results).

    python scripts/mover_completeness.py fetch sparse 167.13 5.17
    python scripts/mover_completeness.py prepare sparse
    python scripts/mover_completeness_far.py base sparse                # the two searches without fakes
    python scripts/mover_completeness_far.py run sparse --trials 24     # 24 rounds of 8 fakes each, 3 to 5 minutes per round and core
    python scripts/mover_completeness_far.py report sparse medium crowded

It uses the files of the bench (``--dir``, default
``.cache/mover-completeness``) and appends one line per round to
``<field>.far.jsonl``. Hours of computing: run it on a workstation, never on
the server of the site.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import subprocess
import sys
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mover_completeness as bench  # noqa: E402
from sphereblink import mover_completeness as mc  # noqa: E402
from sphereblink import mover_stack as ms  # noqa: E402
from sphereblink import movers  # noqa: E402

RAD = 206264.8  # arcsec in a radian
FAKES = 8  # per round
V_MAG = (16.5, 20.5)
AU = (30.0, 110.0)  # drawn evenly in the logarithm
OWN_MOTION = 0.6  # own motion inside this share of the speed that leaves the Sun: what a closed orbit allows
MIN_TIMES = 12  # a fake counts when its place could be seen at this many exposure times
MATCH_ARCSEC = 12.0  # two pixels, at the path's own reference time
MATCH_RATE, MATCH_RATE_MIN = 0.15, 0.25  # the motion: within 15 %, or 0.25 arcsec per hour
ON_FAKE_ARCSEC = 9.2  # a point of a path lies on a fake within 1.5 pixels
BRIGHT_V = 19.0  # "the bright ones" of this search
LONG_PASS_H = 300.0  # a pass of the telescope this long gives the search a path to follow
MOVED_ARCSEC = 40.0  # and so does a body whose path has its ends this far apart
V_BINS = ((16.5, 17.5), (17.5, 18.5), (18.5, 19.0), (19.0, 19.5), (19.5, 20.0), (20.0, 20.5))


@dataclass
class FarFake(mc.Fake):
    """A body at 1/gamma au. Its place at a time is its own motion (v_east,
    v_north, arcsec per hour) plus what Earth's motion does to a body at
    that distance, both counted from mjd0."""

    gamma: float = 0.0  # 1/au

    def offsets(self, mjd) -> tuple[np.ndarray, np.ndarray]:
        mjd = np.atleast_1d(np.asarray(mjd, dtype=float))
        e, n = ms.earth_offsets(np.concatenate([[self.mjd0], mjd]), self.ra0, self.dec0)
        dt = (mjd - self.mjd0) * 24.0
        return self.v_east * dt + self.gamma * (e[1:] - e[0]) * RAD, self.v_north * dt + self.gamma * (n[1:] - n[0]) * RAD

    def at(self, mjd):
        east, north = self.offsets(mjd)
        ra, dec = movers.unproject(east, north, self.ra0, self.dec0)
        return (ra, dec) if np.ndim(mjd) else (float(np.atleast_1d(ra)[0]), float(np.atleast_1d(dec)[0]))

    def velocity(self, mjd: float) -> tuple[float, float]:
        """The motion on the sky at a time, arcsec per hour east and north (Earth's part included)."""
        e, n = self.offsets([mjd - 0.5 / 24.0, mjd + 0.5 / 24.0])
        return float(e[1] - e[0]), float(n[1] - n[0])

    @property
    def rate(self) -> float:
        return math.hypot(*self.velocity(self.mjd0))

    @property
    def pa_deg(self) -> float:
        ve, vn = self.velocity(self.mjd0)
        return math.degrees(math.atan2(ve, vn)) % 360.0


def draw(frames: list[dict], wcs, npix: int, count: int, rng: np.random.Generator) -> list[FarFake]:
    times = np.unique(np.round([float(f["mjd"]) for f in frames], 6))
    out = []
    for k in range(count):
        x, y = rng.uniform(mc.EDGE + 20, npix - 1 - mc.EDGE - 20, 2)
        ra, dec = wcs.pixel_to_world_values(float(x), float(y))
        gamma = 1.0 / math.exp(rng.uniform(math.log(AU[0]), math.log(AU[1])))
        r = OWN_MOTION * ms.ESCAPE * gamma**1.5 * math.sqrt(rng.uniform())  # evenly inside the circle
        pa = rng.uniform(0, 2 * math.pi)
        out.append(FarFake(ra0=float(ra) % 360.0, dec0=float(dec), mjd0=float(rng.choice(times)), v_east=r * math.sin(pa),
                           v_north=r * math.cos(pa), v_mag=float(rng.uniform(*V_MAG)), spectrum="sun", label=k, gamma=gamma))
    return out


def match(track: movers.Track, fake: FarFake) -> tuple[bool, float, float]:
    """Is this path the fake? Its place and its motion at the path's own reference time."""
    ra, dec = fake.at(track.mjd0)
    e, n = movers.project(float(ra), float(dec), track.ra0, track.dec0)
    sep = math.hypot(float(e), float(n))
    ve, vn = fake.velocity(track.mjd0)
    dv = math.hypot(track.v_east - ve, track.v_north - vn)
    return sep <= MATCH_ARCSEC and dv <= max(MATCH_RATE_MIN, MATCH_RATE * math.hypot(ve, vn)), sep, dv


def on_fakes(track: movers.Track, fakes: list[FarFake]) -> dict | None:
    """Whose light a path is made of, point by point: the fake that most of
    its points lie on, the share of them, and how far the path is from that
    fake at its ends. A path that matches no fake at its reference time can
    still follow one for part of the pass."""
    pts = track.extra.get("points") or []
    if not pts or not fakes:
        return None
    mjd = np.array([p["mjd"] for p in pts], dtype=float)
    ra, dec = np.array([p["ra"] for p in pts], dtype=float), np.array([p["dec"] for p in pts], dtype=float)
    cosd = math.cos(math.radians(track.dec0))
    best = None
    for fake in fakes:
        fr, fd = fake.at(mjd)
        sep = np.hypot((ra - fr) * cosd, dec - fd) * 3600.0
        share = float((sep <= ON_FAKE_ARCSEC).mean())
        if best is None or share > best["share"]:
            order = np.argsort(mjd)
            best = {"label": fake.label, "share": round(share, 2), "median_arcsec": round(float(np.median(sep)), 1),
                    "first_arcsec": round(float(sep[order[0]]), 1), "last_arcsec": round(float(sep[order[-1]]), 1)}
    return best


def both(cube: np.ndarray, frames: list[dict], wcs, scale: float) -> tuple[dict, dict, float, float]:
    """The linker, then shift and stack with the linker's tracks kept out, as the site runs them."""
    t0 = time.time()
    a = movers.search(cube, frames, wcs, scale, trials=0)
    t1 = time.time()
    b = ms.search(cube, frames, wcs, scale, exclude=a["tracks"])
    return a, b, round(t1 - t0, 1), round(time.time() - t1, 1)


def brief(t: movers.Track) -> dict:
    x = t.extra
    return {"ra0": round(t.ra0, 5), "dec0": round(t.dec0, 5), "mjd0": round(t.mjd0, 4), "v_east": round(t.v_east, 3), "v_north": round(t.v_north, 3),
            "snr": x.get("stack_snr", x.get("snr")), "strong": bool(x.get("stack_strong")), "distance": x.get("stack_distance"),
            "visits": x.get("stack_visits"), "method": x.get("method")}


_field: dict = {}


def one(index: int) -> dict:
    f = _field
    seed = zlib.crc32(f"{f['name']}:far:{index}".encode())
    rng = np.random.default_rng(seed)
    grid = f["grid"]
    fakes = draw(f["frames"], grid.wcs, grid.npix, FAKES, rng)
    cube = np.array(f["cube"], dtype=np.float32, copy=True)
    told = mc.inject(cube, f["frames"], grid.wcs, grid.scale_arcsec, fakes, seed=seed, noise=f.get("noise"))
    a, b, ta, tb = both(cube, f["frames"], grid.wcs, grid.scale_arcsec)
    rows, used = [], set()
    for fake, sightings in zip(fakes, told):
        snr = np.array([s.get("snr") or 0.0 for s in sightings if s["observable"]])
        row = {**asdict(fake), "au": round(1.0 / fake.gamma, 1), "rate": round(fake.rate, 3), "pa_deg": round(fake.pa_deg, 1),
               **mc.summary(fake, sightings), "snr_sum": round(float(np.sqrt((snr**2).sum())), 2), "frames_in": len(sightings)}
        mine = [t for t in mc.passes(f["frames"]) if np.any(np.abs(t - fake.mjd0) < 1e-5)]
        if mine:  # how long its pass is, and how far apart the ends of its path in it are
            east, north = fake.offsets(mine[0][[0, -1]])
            row.update(pass_hours=round(float(np.ptp(mine[0])) * 24.0, 1), moved_arcsec=round(float(np.hypot(east[1] - east[0], north[1] - north[0])), 1))
        row["linker"] = any(match(t, fake)[0] for t in a["tracks"])
        best = None
        for k, t in enumerate(b["tracks"]):
            ok, sep, dv = match(t, fake)
            if ok:
                used.add(k)
                if best is None or (t.extra.get("stack_snr") or 0) > (best[0].extra.get("stack_snr") or 0):
                    best = (t, sep, dv)
        row.update(found=best is not None, strong=bool(best and best[0].extra.get("stack_strong")))
        if best:
            row.update(sep_arcsec=round(best[1], 2), dv=round(best[2], 3), track=brief(best[0]))
        rows.append(row)
    base = f.get("far_base") or []
    others = [{**brief(t), "on_fake": on_fakes(t, fakes)} for k, t in enumerate(b["tracks"])
              if k not in used and not any(mc.same_path(t, o) for o in base)]
    stats = {k: v for k, v in b.get("stats", {}).items() if isinstance(v, (int, float, dict))}
    return {"field": f["name"], "round": index, "seed": seed, "commit": f["commit"], "version": movers.VERSION,
            "stack_version": getattr(ms, "VERSION", None), "seconds_linker": ta,
            "seconds_stack": tb, "linker_tracks": len(a["tracks"]), "stack_tracks": len(b["tracks"]), "others": others, "stats": stats,
            "fakes": rows}


def _load(args, field: str) -> Path:
    global _field
    _field = bench.load(field, args.dir)
    _field["commit"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    # a distant body stays for the whole pass of the stack search, which is longer than the linker's
    passes = mc.passes
    mc.passes = lambda frames, gap_d=ms.PASS_GAP_D: passes(frames, gap_d)
    return args.dir / f"{field}.{args.tag}-base.pkl"


def base(args) -> None:
    path = _load(args, args.field)
    grid = _field["grid"]
    a, b, ta, tb = both(_field["cube"], _field["frames"], grid.wcs, grid.scale_arcsec)
    path.write_bytes(pickle.dumps({"linker": a["tracks"], "stack": b["tracks"], "stats": b.get("stats")}))
    print(f"{args.field}: linker {len(a['tracks'])} tracks in {ta} s, shift and stack {len(b['tracks'])} tracks in {tb} s")
    for t in b["tracks"]:
        print("  ", brief(t))


def run(args) -> None:
    import multiprocessing

    path = _load(args, args.field)
    _field["far_base"] = pickle.loads(path.read_bytes())["stack"] if path.exists() else []
    out_path = args.dir / f"{args.field}.{args.tag}.jsonl"
    done = {json.loads(line)["round"] for line in out_path.read_text().splitlines() if line.strip()} if out_path.exists() else set()
    jobs = [i for i in range(args.first, args.first + args.trials) if i not in done]
    with multiprocessing.get_context("fork").Pool(args.procs, initializer=os.nice, initargs=(10,)) as pool, out_path.open("a") as out:
        for row in pool.imap_unordered(one, jobs):
            out.write(json.dumps(row) + "\n")
            out.flush()
            fk = row["fakes"]
            print(f"  round {row['round']}: shift and stack {sum(r['found'] for r in fk)} ({sum(r['strong'] for r in fk)} at 7.5 sigma) of {len(fk)}, "
                  f"linker {sum(r['linker'] for r in fk)}, {len(row['others'])} other paths, {row['seconds_linker']} + {row['seconds_stack']} s", flush=True)


def kind_of(other: dict) -> str:
    """What a path that matched no fake is made of: ``follows`` a fake (two thirds of its points or more lie on
    one), ``crosses`` one (some of its points do), or ``nothing`` (none does: a false path)."""
    on = other.get("on_fake")
    if on is None:
        return "unknown"
    return "follows" if on["share"] >= 2 / 3 else "crosses" if on["share"] > 0 else "nothing"


def _count(fakes: list[dict]) -> dict:
    bright = [k for k in fakes if k["v_mag"] < BRIGHT_V]
    return {"fakes": len(fakes), "found": sum(k["found"] for k in fakes), "strong": sum(k["strong"] for k in fakes),
            "bright": len(bright), "bright_found": sum(k["found"] for k in bright)}


def pass_of(fake: dict) -> str:
    """The kind of pass a fake fell into: ``short``, or long with the body nearly ``still``, or ``long``."""
    if fake["pass_hours"] < LONG_PASS_H:
        return "short"
    return "long" if fake["moved_arcsec"] >= MOVED_ARCSEC else "still"


def tally(rows: list[dict]) -> dict:
    """The counts of some rounds: fakes that count, found, at 7.5 sigma, by magnitude, by pass, the distances, the other paths."""
    fakes = [k for r in rows for k in r["fakes"] if k["times"] >= MIN_TIMES]
    found = [k for k in fakes if k["found"]]
    timed = [k for k in fakes if "pass_hours" in k]
    ranged = [(k, k["track"]["distance"]) for k in found if (k["track"].get("distance") or {}).get("distance_au") is not None]
    holds = [k for k, d in ranged if (d.get("distance_min_au") or 0.0) <= k["au"] <= (d.get("distance_max_au") or math.inf)]
    others = [o for r in rows for o in r["others"]]
    kinds = {name: [o for o in others if kind_of(o) == name] for name in ("follows", "crosses", "nothing", "unknown")}
    return {
        "rounds": len(rows), **_count(fakes), "linker": sum(k["linker"] for k in fakes),
        "either": sum(bool(k["found"] or k["linker"]) for k in fakes),
        "passes": {name: _count([k for k in timed if pass_of(k) == name]) for name in ("long", "still", "short")} if timed else {},
        "by_v": [{"v": [lo, hi], "fakes": sum(lo <= k["v_mag"] < hi for k in fakes), "found": sum(lo <= k["v_mag"] < hi for k in found),
                  "strong": sum(lo <= k["v_mag"] < hi and k["strong"] for k in fakes)} for lo, hi in V_BINS],
        "distance": {"given": len(ranged), "holds": len(holds)},
        "others": {name: {"paths": len(group), "strong": sum(o["strong"] for o in group)} for name, group in kinds.items() if group},
        "seconds_stack": float(np.median([r["seconds_stack"] for r in rows])) if rows else None,
    }


def report(args) -> None:
    out, every = {}, []
    for field in args.fields:
        path = args.dir / f"{field}.{args.tag}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        every += rows
        out[field] = tally(rows)
    out["all"] = tally(every)
    for name, t in out.items():
        print(f"{name}: {t['rounds']} rounds, {t['fakes']} fakes count; found {t['found']}, at 7.5 sigma {t['strong']}; brighter than V {BRIGHT_V:g}: "
              f"{t['bright_found']} of {t['bright']}; linker {t['linker']}, either search {t['either']}; distance given for {t['distance']['given']}, "
              f"holds the true one for {t['distance']['holds']}; {t['seconds_stack']} s per search")
        for b in t["by_v"]:
            print(f"   V {b['v'][0]} to {b['v'][1]}: {b['found']} of {b['fakes']} ({b['strong']} at 7.5 sigma)")
        for kind, c in t["passes"].items():
            print(f"   pass {kind}: {c['found']} of {c['fakes']} ({c['bright_found']} of {c['bright']} brighter than V {BRIGHT_V:g})")
        for kind, o in t["others"].items():
            print(f"   paths that matched no fake, {kind}: {o['paths']} ({o['strong']} at 7.5 sigma)")
    if args.out:
        args.out.write_text(json.dumps(out, indent=1))
    if args.into:  # the count the viewer shows, as the key "stack" of its result file
        versions = sorted({r.get("stack_version") for r in every if r.get("stack_version") is not None})
        a = out["all"]
        stack = {"version": versions[-1] if versions else None, "made": args.made, "au": list(AU), "v_mag": list(V_MAG), "min_times": MIN_TIMES,
                 "bright_below": BRIGHT_V, "long_pass_h": LONG_PASS_H, "moved_arcsec": MOVED_ARCSEC,
                 "all": {k: a[k] for k in ("rounds", "fakes", "found", "strong", "bright", "bright_found", "linker", "either", "by_v", "passes", "distance", "others")},
                 "fields": {name: {k: out[name][k] for k in ("fakes", "found", "strong", "bright", "bright_found")} for name in args.fields}}
        whole = json.loads(args.into.read_text())
        whole["stack"] = stack
        args.into.write_text(json.dumps(whole, separators=(",", ":")) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=Path(os.environ.get("MOVER_COMPLETENESS_DIR", ".cache/mover-completeness")))
    p.add_argument("--tag", default="far", help="name of the result files")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("base")
    s.add_argument("field")
    s = sub.add_parser("run")
    s.add_argument("field")
    s.add_argument("--trials", type=int, default=24)
    s.add_argument("--first", type=int, default=0)
    s.add_argument("--procs", type=int, default=min(4, os.cpu_count() or 1))
    s = sub.add_parser("report")
    s.add_argument("fields", nargs="+")
    s.add_argument("--out", type=Path, default=None, help="write the counts to this file as JSON")
    s.add_argument("--into", type=Path, default=None, help="put the counts the viewer shows into its result file (web/data/mover-completeness.json)")
    s.add_argument("--made", default=time.strftime("%Y-%m-%d"), help="date of the measurement")
    args = p.parse_args()
    {"base": base, "run": run, "report": report}[args.command](args)


if __name__ == "__main__":
    main()
