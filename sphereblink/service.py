"""Orchestration: find exposures, fetch cutouts concurrently, cache everything."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx
import numpy as np

from . import config
from .cutout import Frame, Grid, cache_key, cut_async, wave_map_from_file
from .sia import Exposure, find_exposures

EXPOSURE_TTL_S = 24 * 3600


class Store:
    """SQLite cache for exposure lists, wavelength maps and cutout frames."""

    def __init__(self, path=None):
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = str(path or config.CACHE_DIR / "cache.sqlite")
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS exposure_lists (
                    key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS frames (
                    key TEXT PRIMARY KEY, uid TEXT NOT NULL, grid TEXT NOT NULL,
                    created REAL NOT NULL, meta TEXT NOT NULL, image BLOB);
                CREATE INDEX IF NOT EXISTS frames_grid ON frames(grid);
                CREATE TABLE IF NOT EXISTS wave_maps (
                    key TEXT PRIMARY KEY, created REAL NOT NULL, map BLOB NOT NULL);
                """
            )

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            self._local.c = c
        return c

    # exposure lists -----------------------------------------------------
    def get_exposures(self, key: str, max_age: float = EXPOSURE_TTL_S) -> list[Exposure] | None:
        row = self._conn().execute("SELECT created, payload FROM exposure_lists WHERE key=?", (key,)).fetchone()
        if not row or time.time() - row[0] > max_age:
            return None
        return [Exposure(**{k: v for k, v in d.items() if k not in ("uid", "mjd")}) for d in json.loads(row[1])]

    def put_exposures(self, key: str, exps: list[Exposure]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO exposure_lists VALUES (?,?,?)",
                (key, time.time(), json.dumps([e.to_dict() for e in exps])),
            )

    # wavelength maps ------------------------------------------------------
    def get_wave_map(self, key: str) -> np.ndarray | None:
        row = self._conn().execute("SELECT map FROM wave_maps WHERE key=?", (key,)).fetchone()
        return np.load(io.BytesIO(row[0])) if row else None

    def put_wave_map(self, key: str, wmap: np.ndarray) -> None:
        buf = io.BytesIO()
        np.save(buf, wmap.astype(np.float32))
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO wave_maps VALUES (?,?,?)", (key, time.time(), buf.getvalue()))

    # frames ---------------------------------------------------------------
    def get_frame(self, key: str, exposure: Exposure) -> Frame | None | bool:
        """Frame, False if known to miss the grid, None if not cached."""
        row = self._conn().execute("SELECT meta, image FROM frames WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        meta = json.loads(row[0])
        if row[1] is None:
            return False

        def num(k):
            v = meta.get(k)
            return float("nan") if v is None else float(v)

        return Frame(
            exposure=exposure,
            image=np.load(io.BytesIO(row[1])).astype(np.float32),
            wave_um=num("wave_um"),
            bandwidth_um=num("bandwidth_um"),
            flux_ujy=num("flux_ujy"),
            flux_err_ujy=num("flux_err_ujy"),
            flags_at_target=meta.get("flags_at_target", []),
            good_fraction=meta.get("good_fraction", 0.0),
        )

    def put_frame(self, key: str, exposure: Exposure, grid: Grid, frame: Frame | None) -> None:
        if frame is None:
            meta, blob = {"miss": True}, None
        else:
            meta = frame.meta()
            buf = io.BytesIO()
            np.save(buf, frame.image.astype(np.float32))
            blob = buf.getvalue()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?)",
                (key, exposure.uid, grid.key(), time.time(), json.dumps(meta), blob),
            )


@dataclass
class Job:
    id: str
    total: int = 0
    done: int = 0
    failed: int = 0
    status: str = "queued"  # queued | searching | fetching | ready | error
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    frames: list[Frame] = field(default_factory=list)  # grows while fetching

    def public(self) -> dict:
        end = self.finished or time.time()
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "frames": len(self.frames),
            "error": self.error,
            "elapsed_s": round(end - self.started, 1),
        }

    def snapshot(self) -> list[Frame]:
        fr = list(self.frames)
        fr.sort(key=lambda f: (f.exposure.mjd, f.exposure.detector))
        return fr


class Service:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()
        self.jobs: dict[str, Job] = {}
        self.by_view: dict[str, str] = {}
        self.lock = threading.Lock()
        self._wave: dict[str, np.ndarray | None] = {}
        self._wave_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ search
    def exposures(self, ra: float, dec: float) -> list[Exposure]:
        key = f"{ra:.5f}_{dec:.5f}_{','.join(config.COLLECTIONS)}"
        exps = self.store.get_exposures(key)
        if exps is None:
            try:
                exps = find_exposures(ra, dec)
            except Exception:
                # Archive search is down: fall back to a stale list if we have one.
                exps = self.store.get_exposures(key, max_age=float("inf"))
                if exps is None:
                    raise
                return exps
            self.store.put_exposures(key, exps)
        return exps

    # ------------------------------------------------------------------ wavelength
    def wave_map(self, e: Exposure) -> np.ndarray | None:
        key = f"{e.collection}_D{e.detector}"
        if key in self._wave:
            return self._wave[key]
        with self.lock:
            lk = self._wave_locks.setdefault(key, threading.Lock())
        with lk:
            if key in self._wave:
                return self._wave[key]
            wmap = self.store.get_wave_map(key)
            if wmap is None:
                try:
                    wmap = wave_map_from_file(e.http_url)
                    self.store.put_wave_map(key, wmap)
                except Exception:
                    wmap = None
            self._wave[key] = wmap
            return wmap

    # ------------------------------------------------------------------ jobs
    def start(self, grid: Grid) -> Job:
        """Start (or reuse) a background job that gathers every frame of a view."""
        vk = grid.key()
        with self.lock:
            jid = self.by_view.get(vk)
            if jid and self.jobs[jid].status != "error":
                return self.jobs[jid]
            job = Job(id=uuid.uuid4().hex[:12])
            self.jobs[job.id] = job
            self.by_view[vk] = job.id
            if len(self.jobs) > 200:  # forget the oldest finished views (frames stay on disk)
                for old in sorted(self.jobs.values(), key=lambda j: j.started)[:50]:
                    if old.status in ("ready", "error"):
                        self.jobs.pop(old.id, None)
                        self.by_view = {k: v for k, v in self.by_view.items() if v != old.id}
        threading.Thread(target=self._run, args=(job, grid), daemon=True).start()
        return job

    def _run(self, job: Job, grid: Grid) -> None:
        try:
            job.status = "searching"
            exps = self.exposures(grid.ra, grid.dec)
            if len(exps) > config.MAX_FRAMES:  # deep fields: thin evenly in time, keep first and last
                step = (len(exps) - 1) / (config.MAX_FRAMES - 1)
                exps = [exps[round(k * step)] for k in range(config.MAX_FRAMES)]
            job.total = len(exps)
            job.status = "fetching"
            todo = []
            for e in exps:
                k = cache_key(e, grid)
                got = self.store.get_frame(k, e)
                if got is None:
                    todo.append((k, e))
                else:
                    job.done += 1
                    if got is not False:
                        job.frames.append(got)
            if todo:
                # wavelength maps (one per release+detector) are fetched once, in threads
                for e in {f"{e.collection}_D{e.detector}": e for _, e in todo}.values():
                    threading.Thread(target=self.wave_map, args=(e,), daemon=True).start()
                asyncio.run(self._fetch_all(job, grid, todo))
            job.status = "ready"
        except Exception as exc:  # surfaced to the UI
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()

    async def _fetch_all(self, job: Job, grid: Grid, todo: list[tuple[str, Exposure]]) -> None:
        sem = asyncio.Semaphore(config.FETCH_WORKERS)
        limits = httpx.Limits(max_connections=config.FETCH_WORKERS * 2, max_keepalive_connections=config.FETCH_WORKERS * 2)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), limits=limits) as client:

            async def one(key: str, e: Exposure) -> None:
                async with sem:
                    last: Exception | None = None
                    for attempt in range(3):
                        try:
                            wmap = await asyncio.to_thread(self.wave_map, e)
                            f = await cut_async(client, e, grid, wmap)
                            self.store.put_frame(key, e, grid, f)
                            if f is not None:
                                job.frames.append(f)
                            last = None
                            break
                        except Exception as exc:
                            last = exc
                            await asyncio.sleep(1.0 * (attempt + 1))
                    if last is not None:
                        job.failed += 1
                    job.done += 1

            await asyncio.gather(*(one(k, e) for k, e in todo))

    def frames_blocking(self, grid: Grid, timeout: float = 600) -> list[Frame]:
        job = self.start(grid)
        t0 = time.time()
        while job.status not in ("ready", "error") and time.time() - t0 < timeout:
            time.sleep(0.2)
        if job.status != "ready":
            raise RuntimeError(job.error or "timed out")
        return job.snapshot()
