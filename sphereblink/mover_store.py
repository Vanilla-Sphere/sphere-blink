"""What the blind search has found so far: the "Discoveries" log.

One SQLite file next to the cutout cache holds

* the fields that were searched (so a field is searched once per version of
  the search and per set of exposures);
* the detections of every searched field, by sky cell. When a neighbouring
  field is opened later they take part in its linking, so a track continues
  across the border and the log grows with the part of the map people open;
* the moving objects themselves, each under an id that stays the same when
  a later search finds the object again or finds more of its path;
* what visitors think of each object: real motion or false alarm, one voice
  per browser.

Nothing here talks to the network.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time

from . import config, movers, tiles
from .movers import Detection, Track

_local = threading.local()
VOTES = ("real", "false")
# browsers behind one address that may judge the same object: room for a team or a class on one network, not for a script
MAX_VOTERS_PER_ADDRESS = int(os.environ.get("SPHEREX_MAX_VOTERS_PER_ADDRESS", "8"))

# What the log shows: objects of the current search that are not waiting for the catalogues (see ``save_field``)
_SHOWN = "version=? AND instr(payload, '\"pending\": true')=0"

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS fields (
    key TEXT PRIMARY KEY, ra REAL NOT NULL, dec REAL NOT NULL, fov REAL NOT NULL, frames INTEGER NOT NULL,
    version INTEGER NOT NULL, searched REAL NOT NULL, cells TEXT NOT NULL, stats TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS detections (
    exposure TEXT NOT NULL, mjd REAL NOT NULL, ra REAL NOT NULL, dec REAL NOT NULL, snr REAL NOT NULL,
    flux_mjy REAL NOT NULL, npix INTEGER NOT NULL, detector INTEGER NOT NULL, wave_um REAL, cell TEXT NOT NULL,
    field TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS detections_cell ON detections(cell);
CREATE INDEX IF NOT EXISTS detections_field ON detections(field);
CREATE TABLE IF NOT EXISTS movers (
    id TEXT PRIMARY KEY, ra REAL NOT NULL, dec REAL NOT NULL, mjd REAL NOT NULL, rate REAL NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS movers_dec ON movers(dec);
CREATE TABLE IF NOT EXISTS votes (
    mover TEXT NOT NULL, voter TEXT NOT NULL, vote TEXT NOT NULL, address TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (mover, voter));
"""


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    path = str(config.CACHE_DIR / "movers.sqlite")
    if c is None or getattr(_local, "path", "") != path:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(path, timeout=30, check_same_thread=False)
        c.executescript(_SCHEMA)
        _local.c, _local.path = c, path
    return c


def _cell(ra: float, dec: float) -> str:
    return tiles.tile_id(*tiles.cell_of(ra, dec))


def _sep_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a = math.sin(math.radians(dec2 - dec1) / 2) ** 2 + math.cos(math.radians(dec1)) * math.cos(math.radians(dec2)) * math.sin(
        math.radians(ra2 - ra1) / 2) ** 2
    return 2 * math.degrees(math.asin(min(1.0, math.sqrt(a)))) * 3600.0


# ----------------------------------------------------------------------------- fields
def field(key: str) -> dict | None:
    row = _conn().execute("SELECT ra, dec, fov, frames, version, searched, cells, stats FROM fields WHERE key=?",
                          (key,)).fetchone()
    if not row:
        return None
    return {"key": key, "ra": row[0], "dec": row[1], "fov": row[2], "frames": row[3], "version": row[4],
            "searched": row[5], "cells": json.loads(row[6]), "stats": json.loads(row[7])}


def searched(key: str, frames: int) -> bool:
    """Was this field searched by the current search with (about) these exposures?"""
    f = field(key)
    return bool(f) and f["version"] == movers.VERSION and frames <= f["frames"] * 1.05 + 4


def coverage() -> dict:
    """How much of the sky has been searched."""
    c = _conn()
    cells: set[str] = set()
    for (text,) in c.execute("SELECT cells FROM fields WHERE version=?", (movers.VERSION,)):
        cells.update(json.loads(text))
    n = c.execute("SELECT COUNT(*) FROM fields WHERE version=?", (movers.VERSION,)).fetchone()[0]
    return {"fields": n, "cells": sorted(cells), "cell_deg": tiles.STEP}


# ----------------------------------------------------------------------------- detections
def _det(row) -> Detection:
    return Detection(exposure=row[0], mjd=row[1], ra=row[2], dec=row[3], snr=row[4], flux_mjy=row[5], npix=row[6],
                     detector=row[7], wave_um=row[8])


def detections_around(ra: float, dec: float, radius_deg: float, not_field: str = "") -> list[Detection]:
    """Stored detections of other fields within a radius: what a new search links with."""
    step = tiles.STEP
    n = int(math.ceil(radius_deg / step)) + 1
    cosd = max(0.05, math.cos(math.radians(dec)))
    nx = min(tiles.NX // 2, int(math.ceil(radius_deg / cosd / step)) + 1)
    x0, y0 = tiles.cell_of(ra, dec)
    cells = [tiles.tile_id((x0 + dx) % tiles.NX, y0 + dy) for dy in range(-n, n + 1) for dx in range(-nx, nx + 1)
             if 0 <= y0 + dy < tiles.NY]
    out, c = [], _conn()
    for i in range(0, len(cells), 500):
        part = cells[i : i + 500]
        q = ("SELECT exposure, mjd, ra, dec, snr, flux_mjy, npix, detector, wave_um FROM detections WHERE cell IN (%s) "
             "AND field != ?" % ",".join("?" * len(part)))
        out += [_det(r) for r in c.execute(q, (*part, not_field))]
    return [d for d in out if _sep_arcsec(ra, dec, d.ra, d.dec) <= radius_deg * 3600.0]


# ----------------------------------------------------------------------------- moving objects
def _new_id(track: Track) -> str:
    first = min(track.detections, key=lambda d: d.mjd)
    raw = f"{first.exposure}|{first.ra:.5f}|{first.dec:.5f}".encode()
    code = int(hashlib.sha1(raw).hexdigest()[:10], 16)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    tail = "".join(letters[(code >> (5 * k)) & 31] for k in range(4))
    return f"VS-{int(first.mjd)}-{tail}"


def _track(payload: dict) -> Track:
    dets = [Detection(**{k: d.get(k) for k in ("exposure", "mjd", "ra", "dec", "snr", "flux_mjy", "npix", "detector",
                                                 "wave_um")}) for d in payload["detections"]]
    return Track(detections=dets, mjd0=payload["mjd0"], ra0=payload["ra0"], dec0=payload["dec0"], v_east=payload["v_east"],
                 v_north=payload["v_north"], rms_arcsec=payload["rms_arcsec"], n_times=payload["n_times"],
                 n_visits=payload["n_visits"], a_east=payload.get("a_east") or 0.0, a_north=payload.get("a_north") or 0.0)


def _by_exposure(old: list[dict], new: list[dict]) -> list[dict]:
    """Both lists, one entry per exposure, the new one winning."""
    out = {d["exposure"]: d for d in old}
    out.update({d["exposure"]: d for d in new})
    return sorted(out.values(), key=lambda d: d["mjd"])


def _same(c: sqlite3.Connection, track: Track) -> list[tuple[str, dict]]:
    """Stored objects that are this track: found before, or another part of its path."""
    span = max(1.0, track.span_h / 24.0 + 1.0)
    rows = c.execute("SELECT id, payload, version FROM movers WHERE dec BETWEEN ? AND ? AND mjd BETWEEN ? AND ?",
                     (track.dec0 - 2.0, track.dec0 + 2.0, track.mjd0 - span - 1.0, track.mjd0 + span + 1.0)).fetchall()
    out = []
    for mid, text, version in rows:
        payload = json.loads(text)
        if movers.same_object(_track(payload), track):
            payload["_version"] = version
            out.append((mid, payload))
    out.sort(key=lambda r: r[1]["_version"] != movers.VERSION)  # what the current search stored comes first
    return out


def save_field(key: str, ra: float, dec: float, fov: float, frames: int, result: dict, known: dict | None = None,
               pending: set | None = None) -> list[str]:
    """Store what the search of one field found. Returns the ids of its objects.

    ``result`` is what ``movers.search`` gives; ``known`` maps a track's index
    to the catalogue object it was identified with (see ``known_objects.py``).
    The tracks whose index is in ``pending`` are stored but not shown: nobody
    could say yet whether they are known objects, and on their own they are
    too weak to be shown without a name (see ``settle``).
    """
    c = _conn()
    now = time.time()
    half = fov / 120.0
    cosd = max(0.05, math.cos(math.radians(dec)))
    cells = sorted({_cell(ra + dx * tiles.STEP / cosd, max(-90.0, min(90.0, dec + dy * tiles.STEP)))
                    for dy in range(-int(half / tiles.STEP) - 1, int(half / tiles.STEP) + 2)
                    for dx in range(-int(half / tiles.STEP) - 1, int(half / tiles.STEP) + 2)
                    if abs(dx * tiles.STEP) <= half and abs(dy * tiles.STEP) <= half})
    ids = []
    with c:
        c.execute("DELETE FROM detections WHERE field=?", (key,))
        c.executemany("INSERT INTO detections VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            (d.exposure, d.mjd, d.ra, d.dec, d.snr, d.flux_mjy, d.npix, d.detector, d.wave_um, _cell(d.ra, d.dec), key)
            for d in result["detections"]])
        for i, track in enumerate(result["tracks"]):
            payload = track.to_dict()
            payload["fields"] = [key]
            match = (known or {}).get(i)
            if match:
                payload["known"] = match
            same = _same(c, track)
            if same and same[0][1].get("_version") != movers.VERSION:
                # found by an earlier search, whose evidence was judged by other rules: the object keeps its id,
                # its age and its votes, the evidence is the new one alone
                mid = same[0][0]
                created = c.execute("SELECT created FROM movers WHERE id=?", (mid,)).fetchone()[0]
                if not (payload.get("known") or {}).get("checked"):
                    # no catalogue service answers now: the name they gave this object before still holds
                    named = next((o["known"] for _, o in same if (o.get("known") or {}).get("name")), None)
                    if named:
                        payload["known"] = named
            elif same:
                mid, old = same[0]
                # the same object from an overlapping or a neighbouring field: one path, all its exposures
                dets = _by_exposure(old["detections"], payload["detections"])
                joined = movers._track_of([Detection(**{k: d.get(k) for k in (
                    "exposure", "mjd", "ra", "dec", "snr", "flux_mjy", "npix", "detector", "wave_um")}) for d in dets])
                merged = joined.to_dict()
                merged["points"] = _by_exposure(old.get("points", []), payload.get("points", []))
                merged.update(movers.tally([d["mjd"] for d in merged["detections"]], merged["points"]))
                merged["fields"] = sorted(set(old.get("fields", [])) | {key})
                merged["known"] = max((payload.get("known"), old.get("known")), key=lambda k: (
                    bool((k or {}).get("name")), bool((k or {}).get("checked")), k is not None))
                payload, track = merged, joined
                created = c.execute("SELECT created FROM movers WHERE id=?", (mid,)).fetchone()[0]
                for other, _ in same[1:]:  # two stored pieces turn out to be one object
                    c.execute("UPDATE OR IGNORE votes SET mover=? WHERE mover=?", (mid, other))
                    c.execute("DELETE FROM votes WHERE mover=?", (other,))
                    c.execute("DELETE FROM movers WHERE id=?", (other,))
            else:
                mid, created = _new_id(track), now
            payload["id"] = mid
            payload.pop("pending", None)
            if i in (pending or ()) and not (payload.get("known") or {}).get("checked") and not movers.strong(payload):
                payload["pending"] = True
            else:
                ids.append(mid)
            c.execute("INSERT OR REPLACE INTO movers VALUES (?,?,?,?,?,?,?,?,?)",
                      (mid, track.ra0, track.dec0, track.mjd0, track.rate, created, now, movers.VERSION, json.dumps(payload)))
        c.execute("INSERT OR REPLACE INTO fields VALUES (?,?,?,?,?,?,?,?,?)",
                  (key, ra, dec, fov, frames, movers.VERSION, now, json.dumps(cells),
                   json.dumps({**result["stats"], "movers": ids})))
    return ids


def uncompared() -> list[tuple[str, Track, dict]]:
    """Objects of the current search that no catalogue service has answered for yet: id, path, evidence."""
    rows = _conn().execute("SELECT id, payload FROM movers WHERE version=? AND instr(payload, '\"checked\": false')>0",
                           (movers.VERSION,)).fetchall()
    out = []
    for mid, text in rows:
        payload = json.loads(text)
        if (payload.get("known") or {}).get("checked") is False:
            out.append((mid, _track(payload), payload))
    return out


def settle(mid: str, match: dict) -> str:
    """Write down what the catalogues said about an object that waited for them.

    A named object is shown. One without a name is shown when its evidence is
    strong enough for a path without a name (``movers.strong``) and is taken
    out otherwise: it was never shown. Returns "named", "candidate" or "dropped".
    """
    c = _conn()
    with c:
        row = c.execute("SELECT payload FROM movers WHERE id=? AND version=?", (mid, movers.VERSION)).fetchone()
        if not row or not match.get("checked"):
            return "waiting"
        payload = json.loads(row[0])
        if not match.get("name") and not movers.strong(payload):
            c.execute("DELETE FROM votes WHERE mover=?", (mid,))
            c.execute("DELETE FROM movers WHERE id=?", (mid,))
            return "dropped"
        payload.pop("pending", None)
        payload["known"] = match
        c.execute("UPDATE movers SET payload=?, updated=? WHERE id=?", (json.dumps(payload), time.time(), mid))
    return "named" if match.get("name") else "candidate"


def stale_fields() -> list[dict]:
    """Fields an earlier version of the search went through, newest first: what they hold is not shown until the
    current search has gone through them."""
    rows = _conn().execute("SELECT key, ra, dec, fov, frames FROM fields WHERE version!=? ORDER BY searched DESC",
                           (movers.VERSION,)).fetchall()
    return [{"key": r[0], "ra": r[1], "dec": r[2], "fov": r[3], "frames": r[4]} for r in rows]


def _tally(c: sqlite3.Connection, mid: str) -> dict:
    counts = dict(c.execute("SELECT vote, COUNT(*) FROM votes WHERE mover=? GROUP BY vote", (mid,)).fetchall())
    real, false = counts.get("real", 0), counts.get("false", 0)
    verdict = None
    if real + false >= 3:  # a few voices before the log takes a side
        verdict = "real" if real >= 2 * false else "false" if false >= 2 * real else "disputed"
    return {"real": real, "false": false, "verdict": verdict}


def _summary(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in ("detections", "points")}


def get(mid: str, voter: str = "") -> dict | None:
    c = _conn()
    row = c.execute(f"SELECT payload, created, updated FROM movers WHERE {_SHOWN} AND id=?", (movers.VERSION, mid)).fetchone()
    if not row:
        return None
    out = json.loads(row[0])
    out.update(created=row[1], updated=row[2], votes=_tally(c, mid))
    if voter:
        mine = c.execute("SELECT vote FROM votes WHERE mover=? AND voter=?", (mid, voter)).fetchone()
        out["my_vote"] = mine[0] if mine else None
    return out


def listing(ra: float | None = None, dec: float | None = None, radius_deg: float | None = None, limit: int = 200,
            ids: list[str] | None = None) -> list[dict]:
    """The log, newest change first, without the single detections."""
    c = _conn()
    if ids is not None:
        rows = []
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            rows += c.execute(f"SELECT id, payload, created, updated FROM movers WHERE {_SHOWN} AND id IN (%s)"
                              % ",".join("?" * len(part)), [movers.VERSION, *part]).fetchall()
    elif ra is not None and dec is not None and radius_deg is not None:
        rows = c.execute(f"SELECT id, payload, created, updated FROM movers WHERE {_SHOWN} AND dec BETWEEN ? AND ? "
                         "ORDER BY updated DESC", (movers.VERSION, dec - radius_deg, dec + radius_deg)).fetchall()
        rows = [r for r in rows if _sep_arcsec(ra, dec, json.loads(r[1])["ra0"], json.loads(r[1])["dec0"]) <= radius_deg * 3600.0]
    else:
        rows = c.execute(f"SELECT id, payload, created, updated FROM movers WHERE {_SHOWN} ORDER BY updated DESC LIMIT ?",
                         (movers.VERSION, limit)).fetchall()
    out = []
    for mid, text, created, updated in rows[:limit]:
        out.append({**_summary(json.loads(text)), "created": created, "updated": updated, "votes": _tally(c, mid)})
    return out


def count() -> int:
    return _conn().execute(f"SELECT COUNT(*) FROM movers WHERE {_SHOWN}", (movers.VERSION,)).fetchone()[0]


# ----------------------------------------------------------------------------- what visitors think
def vote(mid: str, voter: str, value: str | None, address: str) -> dict:
    """One voice per browser and object; ``None`` takes it back.

    ``address`` is a hash of where the request came from, never the address
    itself. A few browsers behind one address may each judge an object (a
    classroom), not more.
    """
    c = _conn()
    if value is not None and value not in VOTES:
        raise ValueError("vote must be 'real', 'false' or null")
    if c.execute("SELECT 1 FROM movers WHERE id=?", (mid,)).fetchone() is None:
        raise KeyError(mid)
    with c:
        if value is None:
            c.execute("DELETE FROM votes WHERE mover=? AND voter=?", (mid, voter))
        else:
            others = c.execute("SELECT COUNT(*) FROM votes WHERE mover=? AND address=? AND voter!=?",
                               (mid, address, voter)).fetchone()[0]
            if others >= MAX_VOTERS_PER_ADDRESS:
                raise PermissionError("this object already has the votes it accepts from your network")
            c.execute("INSERT INTO votes VALUES (?,?,?,?,?) ON CONFLICT(mover, voter) DO UPDATE SET vote=excluded.vote, "
                      "address=excluded.address, at=excluded.at", (mid, voter, value, address, time.time()))
    return _tally(c, mid)
