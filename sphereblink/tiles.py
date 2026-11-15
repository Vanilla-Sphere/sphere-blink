"""The sky as a fixed grid of 0.12-degree cells, and progressive fields on it.

The team's measurement pipeline addresses the sky by cells of a fixed ICRS
grid: 3000 x 1500 cells of 0.12 degrees in RA and Dec, named
``icrs012-v1-XXXX-YYYY``. A *progressive* run starts at the cell that holds
the requested position (level 0) and grows outwards ring by ring: level N is
the block of (2N+1) x (2N+1) cells around it (N=3: 49 cells, 0.84 degrees).

Here the same addressing drives the images. A field is loaded in stages -
the seed cell, then its first ring, then the whole block - each stage being
one ordinary view centred on the seed cell, so the centre is on screen after
seconds while the rings fill in behind it. Every exposure is read once per
stage for the whole block, not once per cell.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

from . import guard, settings
from .cutout import Grid

STEP = 0.12  # degrees
NX, NY = 3000, 1500
SCHEME = "icrs012-v1"
MAX_LEVELS = 3
# Called with the grid of a field's last stage once all its frames are loaded
# (the blind search for moving objects registers here, see movers_api.py).
listeners: list = []


def cell_of(ra: float, dec: float) -> tuple[int, int]:
    if not (math.isfinite(ra) and math.isfinite(dec) and -90 <= dec <= 90):
        raise ValueError("RA must be finite, Dec within [-90, 90]")
    return int((ra % 360.0) / STEP) % NX, min(NY - 1, int((dec + 90.0) / STEP))


def tile_id(x: int, y: int) -> str:
    return f"{SCHEME}-{x:04d}-{y:04d}"


def parse_tile_id(text: str) -> tuple[int, int]:
    head, _, rest = text.strip().rpartition("-")
    scheme, _, xs = head.rpartition("-")
    if scheme != SCHEME or not (xs.isdigit() and rest.isdigit()):
        raise ValueError(f"tile ids look like {SCHEME}-2245-0789")
    x, y = int(xs), int(rest)
    if not (0 <= x < NX and 0 <= y < NY):
        raise ValueError("tile id outside the grid")
    return x, y


def center(x: int, y: int) -> tuple[float, float]:
    return (x + 0.5) * STEP, (y + 0.5) * STEP - 90.0


def tile(x: int, y: int, level: int) -> dict:
    ra_c, dec_c = center(x, y)
    return {
        "tile_id": tile_id(x, y),
        "ra_c": round(ra_c, 6),
        "dec_c": round(dec_c, 6),
        "level": level,
        # corners (RA, Dec) for drawing the cell on any projection
        "ra_min": round(x * STEP, 6),
        "ra_max": round((x + 1) * STEP, 6),
        "dec_min": round(y * STEP - 90.0, 6),
        "dec_max": round((y + 1) * STEP - 90.0, 6),
    }


def block(ra: float, dec: float, levels: int) -> list[dict]:
    """Cells of a progressive field, centre first, then ring by ring."""
    if not 0 <= levels <= MAX_LEVELS:
        raise ValueError(f"levels must be 0..{MAX_LEVELS}")
    x, y = cell_of(ra, dec)
    out, seen = [], set()
    for level in range(levels + 1):
        for dy in range(-level, level + 1):
            for dx in range(-level, level + 1):
                if max(abs(dx), abs(dy)) != level:
                    continue
                xx, yy = (x + dx) % NX, y + dy
                if not 0 <= yy < NY or (xx, yy) in seen:
                    continue
                seen.add((xx, yy))
                out.append(tile(xx, yy, level))
    return out


def stage_levels(levels: int) -> list[int]:
    """Levels that are loaded as a stage: the seed cell, its first ring, the
    whole block. Skipping the levels between costs ~20% more reading than
    loading the block in one go, and shows the centre many times sooner."""
    return sorted({0, min(1, levels), levels})


def stage_grid(ra_c: float, dec_c: float, level: int) -> Grid:
    return Grid.from_fov(ra_c, dec_c, (2 * level + 1) * STEP * 60.0)


@dataclass
class Field:
    """One progressive field: the seed cell and its stages."""

    seed: dict
    levels: int
    tiles: list[dict]
    stages: list[dict]  # {"level", "grid": Grid, "status", "job": last public job info}
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    last_used: float = field(default_factory=time.time)
    client: str = ""  # who asked first

    def ready_level(self) -> int:
        return max((s["level"] for s in self.stages if s["status"] == "ready"), default=-1)

    def failed(self) -> bool:
        return any(s["status"] == "error" for s in self.stages)

    def public(self) -> dict:
        ready = self.ready_level()
        loading = next((s for s in self.stages if s["level"] > ready), None)
        tiles = []
        for t in self.tiles:
            status = "done" if t["level"] <= ready else "loading" if loading and t["level"] <= loading["level"] else "queued"
            tiles.append({**t, "status": status})
        stages = []
        for s in self.stages:
            g: Grid = s["grid"]
            stages.append({
                "level": s["level"],
                "tiles": sum(1 for t in self.tiles if t["level"] <= s["level"]),
                "ra": g.ra,
                "dec": g.dec,
                "fov": round((2 * s["level"] + 1) * STEP * 60.0, 3),
                "npix": g.npix,
                "status": s["status"],
                "job": s["job"],
            })
        return {
            "scheme": SCHEME,
            "step_deg": STEP,
            "levels": self.levels,
            "seed": self.seed,
            "ready_level": ready,
            "status": "ready" if ready >= self.levels else "error" if self.failed() else "loading",
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
            "stages": stages,
            "tiles": tiles,
        }


class Fields:
    """Runs progressive fields on top of a :class:`~sphereblink.service.Service`."""

    def __init__(self, service):
        self.service = service
        self.fields: dict[str, Field] = {}
        self.lock = threading.Lock()

    def start(self, ra: float, dec: float, levels: int) -> Field:
        tiles = block(ra, dec, levels)
        seed = tiles[0]
        key = f"{seed['tile_id']}/{levels}"
        with self.lock:
            f = self.fields.get(key)
            if f is not None and not f.failed():
                f.last_used = time.time()
                return f
            client = guard.current()
            if client and not guard.internal(client):
                loading = sum(1 for o in self.fields.values() if o.client == client and not o.finished)
                if loading >= settings.get("max_fields_per_client"):
                    raise guard.Busy("too many fields are loading for your address; wait for one to finish", 15.0)
            f = Field(seed=seed, levels=levels, tiles=tiles, client=client, stages=[
                {"level": lv, "grid": stage_grid(seed["ra_c"], seed["dec_c"], lv), "status": "queued", "job": None}
                for lv in stage_levels(levels)
            ])
            self.fields[key] = f
            if len(self.fields) > 400:  # bookkeeping only; the frames live in the service
                for k in sorted(self.fields, key=lambda k: self.fields[k].last_used)[:100]:
                    if self.fields[k] is not f:
                        self.fields.pop(k)
        threading.Thread(target=self._run, args=(f,), daemon=True).start()
        return f

    def peek(self, ra: float, dec: float, levels: int) -> Field | None:
        """The field of a place if the server holds one, as it is (never starts
        one): a field that failed is given as failed, where ``start`` would
        replace it by a new attempt."""
        seed = block(ra, dec, levels)[0]
        with self.lock:
            f = self.fields.get(f"{seed['tile_id']}/{levels}")
            if f is not None:
                f.last_used = time.time()
            return f

    def _run(self, f: Field) -> None:
        for s in f.stages[1:]:  # look the later stages up while the first one loads
            threading.Thread(target=self._search, args=(s["grid"],), daemon=True).start()
        # One stage at a time: they would only compete for the same worker
        # processes, and the small one should win. The field keeps the job's
        # numbers, not the job, so the service stays free to drop its frames
        # from memory.
        for s in f.stages:
            # the field was admitted as a whole in start(): its stages are not counted again
            job = self.service.start(s["grid"], client=f.client, capped=False)
            while job.status not in ("ready", "error"):
                s["status"], s["job"] = job.status, job.public()
                time.sleep(0.25)
            s["status"], s["job"] = job.status, job.public()
            if job.status == "error":
                break
        f.finished = time.time()
        if not f.failed():
            for tell in list(listeners):
                try:
                    tell(f.stages[-1]["grid"])
                except Exception:
                    pass  # a listener's trouble is not the field's

    def _search(self, grid: Grid) -> None:
        try:
            self.service.view_exposures(grid)
        except Exception:
            pass  # the stage's own job will report it
