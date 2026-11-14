"""The numbers of the completeness measurement, from the rounds that ``mover_completeness.py run`` wrote.

Writes one JSON file that the viewer and the documentation read
(``web/data/mover-completeness.json``) and, with ``--plot``, a picture of
the same numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from sphereblink import mover_completeness as mc
from sphereblink import movers

V_EDGES = [15.5, 16.0, 16.5, 17.0, 17.25, 17.5, 17.75, 18.0, 18.25, 18.5, 18.75, 19.0, 19.4, 19.8]
V_COARSE = [15.5, 16.5, 17.0, 17.5, 18.0, 18.5, 19.0, 19.8]
RATE_EDGES = [2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 400.0]
SNR_EDGES = [0.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 12.0, 20.0, 50.0, 1000.0]
BRIGHT = 17.5  # brighter than this, brightness is not what loses an object
MIN_TIMES = movers.CANDIDATE_TIMES

# The fields of the measurement: what a visitor would call them, and where they are.
FIELDS = {
    "sparse": {"label": "Few stars", "where": "Leo, 57° from the plane of the Milky Way"},
    "medium": {"label": "More stars", "where": "Ophiuchus, 20° from the plane"},
    "crowded": {"label": "Crowded", "where": "around Barnard's star, 14° from the plane"},
}

# Asteroids and the comet that the search found in these fields before any fake was added, with the visual
# magnitude the catalogue predicted for them (docs/moving-objects.md).
REAL = [
    {"name": "(5) Astraea", "v": 11.8, "rate": 15.7}, {"name": "(412) Elisabetha", "v": 13.9, "rate": 18.9},
    {"name": "(159) Aemilia", "v": 14.0, "rate": 10.8}, {"name": "(2448) Sholokhov", "v": 16.1, "rate": 30.2},
    {"name": "(1494) Savo", "v": 16.4, "rate": 57.3}, {"name": "(8895) Nha", "v": 16.9, "rate": 40.3},
    {"name": "(8838) 1989 UW2", "v": 17.3, "rate": 28.7}, {"name": "(3829) Gunma", "v": 17.3, "rate": 31.7},
    {"name": "(1673) van Houten", "v": 17.3, "rate": 40.9}, {"name": "(3780) Maury", "v": 17.9, "rate": 31.4},
    {"name": "(15101) 2000 AY150", "v": 18.4, "rate": 48.2},
]


def _rounds(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _curve(rows: list[dict], key: str, edges: list[float], level: str, rate=(RATE_EDGES[0], RATE_EDGES[-1])) -> list[dict]:
    cells = mc.table(rows, key, edges, [rate[0], rate[1]], level=level, min_times=MIN_TIMES)
    return [{"from": c[key][0], "to": c[key][1], "n": c["n"], "found": c["found"], "share": c["share"], "low": c["low"],
             "high": c["high"]} for c in cells]


def _limits(rows: list[dict], level: str) -> dict:
    fit = mc.half_point(rows, "v_mag", level, 0.5, MIN_TIMES)
    if not fit:
        return {}
    top = fit["plateau"]
    out = {"plateau": round(top, 3), "half": fit["at"], "width": fit["width"], "n": fit["n"]}
    # where the curve has fallen to half of its plateau, and to nine tenths of it
    out["half_of_plateau"] = round(fit["mid"], 2)
    out["nine_tenths_of_plateau"] = round(fit["mid"] - fit["width"] * math.log(9.0), 2)
    out["half_mjy"] = round(float(mc.flux_mjy(fit["mid"], 1.24, "sun")), 3)  # that magnitude as flux at 1.24 micron
    return out


def _lost(rows: list[dict], level: str = "found") -> dict:
    """Why objects bright enough to be seen in every exposure were not found."""
    bright = [r for r in rows if r["times"] >= MIN_TIMES and r["v_mag"] < BRIGHT]
    out = {"bright": len(bright), "found": sum(bool(r[level]) for r in bright), "short_path": 0, "few_visits": 0, "on_stars": 0,
           "other": 0}
    for r in bright:
        if r[level]:
            continue
        if r["rate"] * r["hours"] < movers.MIN_SHIFT:
            out["short_path"] += 1  # it moved less than three pixels while it was watched
        elif r["visits"] < 3:
            out["few_visits"] += 1
        elif r["detected_times"] < 0.5 * r.get("times_5", r["times"]):
            out["on_stars"] += 1  # bright enough, yet not detected: in front of stars, where the search does not look
        else:
            out["other"] += 1
    return out


def summarise(rounds: list[dict], spectrum: str | None = None) -> dict:
    rows = [r for t in rounds for r in t["fakes"] if spectrum is None or r.get("spectrum", "sun") == spectrum]
    use = [r for r in rows if r["times"] >= MIN_TIMES]
    out = {"rounds": len(rounds), "fakes": len(rows), "observable": len(use),
           "seen_too_briefly": len(rows) - len(use),
           "two_visits_only": sum(r["visits"] == 2 for r in use)}
    for level in ("found", "strong"):
        out[level] = {
            "curve": _curve(rows, "v_mag", V_EDGES, level),
            "limits": _limits(rows, level),
            "grid": [[{k: c[k] for k in ("n", "found", "share")} for c in mc.table(rows, "v_mag", V_COARSE, RATE_EDGES, level, MIN_TIMES)
                      if c["rate"][0] == r0] for r0 in RATE_EDGES[:-1]],
            "by_signal": _curve([r for r in rows if r.get("snr") is not None], "snr", SNR_EDGES, level),
            "by_direction": [],
            "lost": _lost(rows, level),
        }
        bright = [r for r in use if r["v_mag"] < BRIGHT]
        for name, lo in (("north", 315.0), ("east", 45.0), ("south", 135.0), ("west", 225.0)):
            cell = [r for r in bright if (r["pa_deg"] - lo) % 360.0 < 90.0]
            got = sum(bool(r[level]) for r in cell)
            out[level]["by_direction"].append({"towards": name, "n": len(cell), "found": got,
                                               "share": round(got / len(cell), 3) if cell else None})
    found = [r for r in use if r["found"] and r.get("sep_arcsec") is not None]
    if found:
        out["accuracy"] = {"position_arcsec": round(float(np.median([r["sep_arcsec"] for r in found])), 2),
                           "speed_arcsec_h": round(float(np.median([abs(r["rate_error"]) for r in found])), 3),
                           "speed_percent": round(float(np.median([abs(r["rate_error"]) / r["rate"] for r in found])) * 100, 2)}
    others = [o for t in rounds for o in t["others"]]
    out["other_paths"] = {
        "all": len(others), "strong": sum(bool(o.get("strong")) for o in others),
        "strong_joining_two_fakes": sum(bool(o.get("strong")) and len(o.get("of_fakes", {})) >= 2 for o in others),
        "strong_without_a_fake": sum(bool(o.get("strong")) and "of_fakes" in o and not o["of_fakes"] for o in others),
        "per_round_limit_95": round(mc.upper_limit(sum(bool(o.get("strong")) for o in others), len(rounds)), 4) if rounds else None,
    }
    out["real_objects_kept"] = {"found": sum(t["baseline_found"] for t in rounds), "of": sum(t["baseline"] for t in rounds)}
    return out


def build(args) -> None:
    fields, cold_fields, every = {}, {}, []
    shuffled = {"searches": 0, "paths": 0}
    loaded = {name: _rounds(args.dir / f"{name}.{args.search}.jsonl") for name in args.fields}
    kinds = {r.get("spectrum", "sun") for rounds in loaded.values() for t in rounds for r in t["fakes"]}
    main = "asteroid" if "asteroid" in kinds else "sun"  # what the figures are about; bodies without heat come second
    for name, rounds in loaded.items():
        if not rounds:
            continue
        every += rounds
        meta = json.loads((args.dir / f"{name}.json").read_text())
        f = {**FIELDS.get(name, {"label": name, "where": ""}), "ra": round(meta["grid"]["ra"], 3), "dec": round(meta["grid"]["dec"], 3),
             "exposures": len(meta["frames"]), **summarise(rounds, main)}
        stats = rounds[0].get("stats", {})
        f["masked_fraction"] = stats.get("masked_fraction")
        sh = _rounds(args.dir / f"{name}.shuffle.jsonl")
        f["shuffled"] = {"searches": len(sh), "paths": int(sum(s["tracks"] for s in sh)),
                         "candidates": int(sum(s.get("candidates", 0) for s in sh))}
        shuffled["searches"] += len(sh)
        shuffled["paths"] += f["shuffled"]["paths"]
        fields[name] = f
        if main != "sun" and "sun" in kinds:
            c = summarise(rounds, "sun")
            cold_fields[name] = {"fakes": c["fakes"], "observable": c["observable"],
                                 **{level: {"curve": c[level]["curve"], "limits": c[level]["limits"]} for level in ("found", "strong")}}
    out = {
        "search": args.search, "version": movers.VERSION, "made": args.made,
        "fake": {"spectrum": main, "v_mag": [V_EDGES[0], V_EDGES[-1]], "rate_arcsec_h": [RATE_EDGES[0], RATE_EDGES[-1]],
                 "min_times": MIN_TIMES, "bright_below": BRIGHT, "asteroid": mc.ASTEROID},
        "v_edges": V_EDGES, "v_coarse": V_COARSE, "rate_edges": RATE_EDGES,
        "all": summarise(every, main), "fields": fields, "real": REAL,
        "shuffled": {**shuffled, "limit_95": round(mc.upper_limit(shuffled["paths"], shuffled["searches"]), 4) if shuffled["searches"] else None},
    }
    if cold_fields:
        c = summarise(every, "sun")
        out["cold"] = {"all": {"fakes": c["fakes"], "observable": c["observable"],
                               **{level: {"curve": c[level]["curve"], "limits": c[level]["limits"], "grid": c[level]["grid"]}
                                  for level in ("found", "strong")}},
                       "fields": cold_fields}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    a = out["all"]
    print(f"{a['rounds']} rounds, {a['fakes']} fakes, {a['observable']} observable at {MIN_TIMES} times or more")
    for level in ("found", "strong"):
        print(f"  {level}: {a[level]['limits']}  lost bright: {a[level]['lost']}")
    for name, f in fields.items():
        print(f"  {name}: found {f['found']['limits']}  strong {f['strong']['limits']}  others {f['other_paths']}  shuffled {f['shuffled']}")
    print(f"  shuffled: {out['shuffled']}  accuracy: {a.get('accuracy')}")
    if "cold" in out:
        print(f"  without heat: found {out['cold']['all']['found']['limits']}  strong {out['cold']['all']['strong']['limits']}")
    if args.plot:
        from mover_completeness_plot import draw

        draw(out, args.plot)
