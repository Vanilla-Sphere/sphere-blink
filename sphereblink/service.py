"""Orchestration: find exposures, fetch cutouts concurrently, cache everything."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import multiprocessing
import os
import queue
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import httpx
import numpy as np

from . import config, guard, retry, settings, worker
from .cutout import Frame, Grid, cache_key, mirror_url, wave_map_from_file
from .sia import Exposure, find_exposures_at

EXPOSURE_TTL_S = 24 * 3600
SEARCH_TIMEOUT_S = 60.0  # archive search for a place never seen before: asked again until then
REFRESH_TIMEOUT_S = 30.0  # ... and for a place whose older list is on disk (nobody waits for this one)
REFRESH_PAUSE_S = 300.0  # after a refresh that failed, the older list serves this long before the next try
REFRESHES_AT_ONCE = 4
HALF_PRECISION_NPIX = 200  # frames wider than this are stored as float16


log = logging.getLogger("uvicorn.error")  # the server's own log: these lines show in the service's log


def say(exc: Exception) -> str:
    """What went wrong with a view, for the person looking at it: a sentence
    when an outside service is away, otherwise the kind of error and a short
    text. Never the address that was asked (the full text goes to the log)."""
    if isinstance(exc, retry.Unavailable):
        return str(exc)
    if isinstance(exc, httpx.HTTPError):
        return f"A data service is not answering right now ({retry.reason_of(exc)}). This usually passes within minutes."
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:157] + '...' if len(text) > 160 else text}"


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
                CREATE TABLE IF NOT EXISTS catalogs (
                    key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS views (
                    grid TEXT PRIMARY KEY, bytes INTEGER NOT NULL, used REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS names (
                    key TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL);
                """
            )
        self._touched: dict[str, float] = {}

    def count_views(self) -> None:
        """A cache written before the ``views`` table existed: count it once
        (reads the whole frame index, so it runs after the server has started)."""
        with self._conn() as c:
            if c.execute("SELECT 1 FROM views LIMIT 1").fetchone() is None:
                c.execute("INSERT OR IGNORE INTO views SELECT grid, COALESCE(SUM(LENGTH(image)), 0), MAX(created) "
                          "FROM frames GROUP BY grid")

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, far fewer fsyncs
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

    # overlay catalogues -------------------------------------------------
    def get_catalog(self, key: str) -> list[dict] | None:
        row = self._conn().execute("SELECT payload FROM catalogs WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_catalog(self, key: str, sources: list[dict]) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO catalogs VALUES (?,?,?)", (key, time.time(), json.dumps(sources)))

    # resolved object names ------------------------------------------------
    def get_name(self, key: str, max_age: float) -> dict | None:
        row = self._conn().execute("SELECT created, payload FROM names WHERE key=?", (key,)).fetchone()
        return json.loads(row[1]) if row and time.time() - row[0] <= max_age else None

    def put_name(self, key: str, target: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO names VALUES (?,?,?)", (key, time.time(), json.dumps(target)))
            if c.execute("SELECT COUNT(*) FROM names").fetchone()[0] > 50000:  # keep the newest half
                c.execute("DELETE FROM names WHERE created < (SELECT created FROM names ORDER BY created DESC LIMIT 1 OFFSET 25000)")

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

    def put_frames(self, grid: Grid, items: list[tuple[str, Exposure, Frame | None]]) -> None:
        rows, now = [], time.time()
        for key, exposure, frame in items:
            if frame is None:
                meta, blob = {"miss": True}, None
            else:
                meta = frame.meta()
                buf = io.BytesIO()
                if frame.image.shape[0] > HALF_PRECISION_NPIX:
                    # wide mosaics: half precision is ~4 significant digits, far
                    # below the noise, and halves the largest items on disk
                    np.save(buf, np.clip(frame.image, -6e4, 6e4).astype(np.float16))
                else:
                    np.save(buf, frame.image.astype(np.float32))
                blob = buf.getvalue()
            rows.append((key, exposure.uid, grid.key(), now, json.dumps(meta), blob))
        size = sum(len(r[5]) for r in rows if r[5] is not None)
        with self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?)", rows)
            c.execute("INSERT INTO views VALUES (?,?,?) ON CONFLICT(grid) DO UPDATE SET bytes = bytes + excluded.bytes, "
                      "used = excluded.used", (grid.key(), size, now))

    def put_frame(self, key: str, exposure: Exposure, grid: Grid, frame: Frame | None) -> None:
        self.put_frames(grid, [(key, exposure, frame)])

    def touch(self, grid_key: str) -> None:
        """Note that a view was opened (at most once a minute per view)."""
        now = time.time()
        if now - self._touched.get(grid_key, 0.0) < 60.0:
            return
        if len(self._touched) > 5000:
            self._touched.clear()
        self._touched[grid_key] = now
        with self._conn() as c:
            c.execute("UPDATE views SET used=? WHERE grid=?", (now, grid_key))

    def trim(self, max_bytes: float, keep: "str | set[str]" = "") -> int:
        """Drop the views nobody opened for the longest time until the frames
        fit in ``max_bytes`` (never the views in ``keep``). Returns the bytes freed."""
        keep = {keep} if isinstance(keep, str) else set(keep)
        c = self._conn()
        views = c.execute("SELECT grid, bytes FROM views ORDER BY used").fetchall()
        total = sum(v[1] or 0 for v in views)
        freed = 0
        for grid_key, size in views:
            if total - freed <= max_bytes:
                break
            if grid_key in keep:
                continue
            with c:
                c.execute("DELETE FROM frames WHERE grid=?", (grid_key,))
                c.execute("DELETE FROM views WHERE grid=?", (grid_key,))
            freed += size or 0
        return freed


class Scheduler:
    """Shares the fetch processes between the views being gathered.

    Every view hands in its exposures as chunks. A limited number of views
    is worked on at a time: ``active_views`` of them, holding together no
    more than ``active_mb`` of frames (which is what keeps several wide
    mosaics from filling the memory). Both are settings that can change
    while the server runs (``settings.py``); :meth:`recheck` puts a change
    in force. The others wait in a queue, and :meth:`place` tells each one
    its position:

    * the queue is first come, first served, with the callers taking turns
      (the first view of every caller comes before anybody's second one), so
      one caller with many views cannot keep the others waiting;
    * a view too heavy for the memory that is left waits for a heavy one to
      finish, and lighter views behind it go ahead meanwhile;
    * a view in the queue still gets its first few exposures (one small
      chunk) unless the setting ``queue_preview`` is off, so there is a picture
      on screen while it waits;
    * among the views being worked on, the one with the least left to do
      goes first and every fourth chunk goes to the one that started first.

    Only a few chunks are in the worker processes at a time; whenever one
    comes back, the next is chosen.
    """

    def __init__(self, pool_of, slots: int):
        self._pool_of, self._slots = pool_of, slots
        self._lock = threading.Lock()
        self._views: dict[str, dict] = {}  # job id -> {client, started, pending, sent, out, active, mb}
        self._flying = 0
        self._picks = 0
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        # Chunks are handed over from a thread of our own, never from the
        # pool's callback (which may hold the pool's locks).
        while True:
            self._wake.wait()
            self._wake.clear()
            try:
                self._pump()
            except Exception:  # pragma: no cover - keep the dispatcher alive whatever happens
                time.sleep(0.2)
                self._wake.set()

    def run(self, job_id: str, client: str, grid: Grid, chunks: list[list[worker.Item]], spilled: bool = False):
        """Cut the chunks of one view; yields (results, stats) as each comes back.
        ``spilled``: its frames go to a file on disk (``Spill``), not into memory."""
        out: "queue.Queue" = queue.Queue()
        # what its frames will take in memory while they are gathered (float32, every exposure a frame);
        # frames that go to disk only pass through, a chunk or two at a time
        frames = max(map(len, chunks), default=0) * 2 if spilled else sum(len(c) for c in chunks)
        mb = frames * getattr(grid, "npix", 0) ** 2 * 4 / 1e6
        with self._lock:
            self._views[job_id] = {"client": client, "started": time.time(), "grid": grid, "pending": list(reversed(chunks)),
                                   "sent": 0, "out": out, "active": False, "mb": mb}
            self._admit()
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="fetch-scheduler")
                self._thread.start()
        try:
            self._wake.set()
            for _ in range(len(chunks)):
                got = out.get()
                if isinstance(got, BaseException):
                    raise got
                yield got
        finally:
            with self._lock:
                self._views.pop(job_id, None)
                self._admit()
            self._wake.set()  # the next view in the queue has its turn

    def _queue(self) -> list[dict]:
        """The waiting views in the order they will be taken (lock held)."""
        turn: dict[str, int] = {}  # how many views of this caller are ahead of the next one
        for v in self._views.values():
            if v["active"]:
                turn[v["client"]] = turn.get(v["client"], 0) + 1
        ranked = []
        for v in sorted((v for v in self._views.values() if not v["active"]), key=lambda v: v["started"]):
            ranked.append((turn.get(v["client"], 0), v["started"], v))
            turn[v["client"]] = turn.get(v["client"], 0) + 1
        return [v for _, _, v in sorted(ranked, key=lambda r: r[:2])]

    def _admit(self) -> None:
        """Give the free places to the first views of the queue that fit (lock held)."""
        views, room = settings.get("active_views"), settings.get("active_mb")
        limit = views if views > 0 else len(self._views)
        active = [v for v in self._views.values() if v["active"]]
        n, mb = len(active), sum(v["mb"] for v in active)
        for v in self._queue():
            if n >= limit:
                break
            if n and room > 0 and mb + v["mb"] > room:
                continue  # too heavy for the memory that is left: it waits, lighter ones behind it may go
            v["active"] = True
            n, mb = n + 1, mb + v["mb"]

    def recheck(self) -> None:
        """The limits changed: views that fit now have their turn. (A view
        being worked on keeps its place when the limits went down.)"""
        with self._lock:
            self._admit()
        self._wake.set()

    def place(self, job_id: str) -> tuple[int, int]:
        """(position in the queue, views waiting); position 0: being worked on."""
        with self._lock:
            v = self._views.get(job_id)
            if v is None or v["active"]:
                return 0, 0
            waiting = self._queue()
            return next(i for i, w in enumerate(waiting, 1) if w is v), len(waiting)

    def _next(self) -> "dict | None":
        active = [v for v in self._views.values() if v["active"] and v["pending"]]
        ahead = self._queue() if settings.get("queue_preview") else []
        fresh = [v for v in active + ahead if v["sent"] == 0 and v["pending"]]
        if not active and not fresh:
            return None
        self._picks += 1
        if fresh:  # the first few exposures cost little: no view waits for them
            return min(fresh, key=lambda v: v["started"])
        if self._picks % 4 == 0:
            return min(active, key=lambda v: v["started"])
        return min(active, key=lambda v: (len(v["pending"]), v["started"]))

    def _pump(self) -> None:
        while True:
            with self._lock:
                if self._flying >= self._slots:
                    return
                v = self._next()
                if v is None:
                    return
                chunk = v["pending"].pop()
                v["sent"] += 1
                self._flying += 1
            try:
                fut = self._pool_of().submit(worker.cut_chunk, v["grid"], chunk)
            except BaseException as exc:  # the pool is broken or shutting down
                with self._lock:
                    self._flying -= 1
                v["out"].put(exc)
                continue
            fut.add_done_callback(lambda f, v=v: self._done(v, f))

    def _done(self, v: dict, fut) -> None:
        with self._lock:
            self._flying -= 1
        try:
            v["out"].put(fut.result())
        except BaseException as exc:
            v["out"].put(exc)
        self._wake.set()


def disk_array(shape: tuple[int, ...]) -> np.memmap | None:
    """A float32 array in a file on disk without a name (see ``Spill``), or
    None when the disk has no room for it."""
    try:
        folder = config.CACHE_DIR / "spill"
        folder.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=folder, suffix=".f32")
        try:
            # taken on disk now: a full disk is an error here, not a crash of
            # the process (SIGBUS) when the array is written to later
            os.posix_fallocate(fd, 0, max(1, int(np.prod(shape))) * 4)
            return np.memmap(path, dtype=np.float32, mode="r+", shape=shape)
        finally:
            os.close(fd)
            os.unlink(path)  # the mapping keeps the file for as long as it is open
    except (OSError, ValueError) as exc:
        log.warning("no room on disk for %s frames, they stay in memory: %s", shape, exc)
        return None


class Spill:
    """The frames of one wide view, in a file on disk instead of the memory
    of the process (``config.SPILL_NPIX``).

    Each frame's image becomes a window on the file. What is being read sits
    in the kernel's file cache, which the kernel takes back when the memory is
    needed; the same frames held by the process could not be given back, and a
    7 x 7 field (0.5-1.2 GB of them) was enough to fill a 2 GB server. The file
    has no name: it goes away by itself once nothing refers to its frames any
    more, so a restart or a crash leaves nothing behind.
    """

    def __init__(self, mm: np.memmap):
        self.mm, self.used = mm, 0
        self._lock = threading.Lock()

    @classmethod
    def open(cls, frames: int, npix: int) -> "Spill | None":
        """Room for ``frames`` frames, or None (the frames then stay in memory)
        when the disk has no room for them."""
        mm = disk_array((frames, npix, npix)) if frames > 0 else None
        return cls(mm) if mm is not None else None

    @property
    def nbytes(self) -> int:
        return self.mm.nbytes

    def frame_bytes(self) -> int:
        return self.mm[0].nbytes if len(self.mm) else 0

    def keep(self, frame: Frame) -> Frame:
        """The frame, its image moved into the file (where there is room)."""
        if frame.image.shape != self.mm.shape[1:]:
            return frame
        with self._lock:
            i = self.used
            if i >= len(self.mm):
                return frame  # more frames than announced: the others stay in memory
            self.used += 1
        self.mm[i] = frame.image
        frame.image = np.asarray(self.mm[i])  # a plain array on the file's memory
        return frame


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
    last_used: float = field(default_factory=time.time)
    stats: dict = field(default_factory=dict)  # where the time went (seconds, MB)
    resuming: bool = True  # still looking up what the disk cache already has
    client: str = ""  # who asked first (for sharing the fetch processes fairly)
    frames: list[Frame] = field(default_factory=list)  # grows while fetching
    place: "Callable[[], tuple[int, int]] | None" = None  # while its exposures are cut: where it is in the queue
    spill: Spill | None = None  # a wide view: its frames are in a file on disk

    def keep(self, frame: Frame) -> None:
        self.frames.append(self.spill.keep(frame) if self.spill is not None else frame)

    def held(self) -> tuple[int, int]:
        """Bytes of frames (in the memory of the process, in a file on disk)."""
        total = sum(f.image.nbytes for f in self.frames)
        disk = self.spill.used * self.spill.frame_bytes() if self.spill is not None else 0
        return total - disk, (self.spill.nbytes if self.spill is not None else 0)

    def public(self) -> dict:
        end = self.finished or time.time()
        position, waiting = self.place() if self.place and self.status == "fetching" else (0, 0)
        return {
            "id": self.id,
            # "queued" with a position: the pipeline is busy with other views and this one waits its turn
            "status": "queued" if position else self.status,
            "queue": {"position": position, "waiting": waiting} if position else None,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "frames": len(self.frames),
            "error": self.error,
            "elapsed_s": round(end - self.started, 1),
            "stats": {k: round(v, 2) for k, v in self.stats.items()},
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
        self._search_locks: dict[str, threading.Lock] = {}
        self._refreshing: dict[str, float] = {}  # exposure list -> inf while asked again, else when that last failed
        self._procs: ProcessPoolExecutor | None = None
        # a couple more chunks than processes are handed over, so no process waits for the next one
        self._sched = Scheduler(self._pool, config.FETCH_PROCS + 2)
        self._in_loop, self._in_loop_turn = 0, threading.Condition()  # one core: views cut at once (see _one_core)
        self.settings_changed = self._sched.recheck  # for settings.on_change
        self.pinned: set[str] = set()  # views the disk cache never drops (the featured targets)

    # ------------------------------------------------------------------ search
    def exposures(self, ra: float, dec: float) -> list[Exposure]:
        """Exposures that cover a position."""
        return self._exposures(f"{ra:.5f}_{dec:.5f}", [(ra, dec)])

    def view_exposures(self, grid: Grid) -> list[Exposure]:
        """Exposures that make up a view (wide views: also partial coverage)."""
        pts = grid.search_points()
        if len(pts) == 1:
            return self.exposures(grid.ra, grid.dec)
        return self._exposures(f"view_{grid.key()}_{len(pts)}", pts)

    def _exposures(self, where: str, points: list[tuple[float, float]]) -> list[Exposure]:
        key = f"{where}_{','.join(config.COLLECTIONS)}"
        with self.lock:
            lk = self._search_locks.setdefault(key, threading.Lock())
        with lk:  # a search that is already running is waited for, not repeated
            exps = self.store.get_exposures(key)
            if exps is None:
                # A list older than a day is still the right answer for nearly
                # all of its exposures: it is given at once and brought up to
                # date behind the scenes, so a place seen before opens the same
                # whether the archive search is quick, slow or down.
                exps = self.store.get_exposures(key, max_age=float("inf"))
                if exps is not None:
                    self._refresh_later(key, points)
                else:
                    exps = find_exposures_at(points, timeout=SEARCH_TIMEOUT_S)
                    self.store.put_exposures(key, exps)
        with self.lock:
            if len(self._search_locks) > 2000:
                self._search_locks.clear()
        return exps

    def _refresh_later(self, key: str, points: list[tuple[float, float]]) -> None:
        """Ask the archive for the current list of a place in the background."""
        now = time.time()
        with self.lock:
            if len(self._refreshing) > 2000:
                self._refreshing = {k: t for k, t in self._refreshing.items() if now - t < REFRESH_PAUSE_S}
            running = sum(1 for t in self._refreshing.values() if t == float("inf"))
            if now - self._refreshing.get(key, 0.0) < REFRESH_PAUSE_S or running >= REFRESHES_AT_ONCE:
                return
            self._refreshing[key] = float("inf")  # running

        def run() -> None:
            try:
                self.store.put_exposures(key, find_exposures_at(points, timeout=REFRESH_TIMEOUT_S))
                done = 0.0  # fresh for a day: the next request does not come here
            except Exception as exc:
                log.warning("exposure list %s not refreshed: %s", key, exc)
                done = time.time()
            with self.lock:
                self._refreshing[key] = done

        threading.Thread(target=run, daemon=True, name="refresh-exposures").start()

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
                for url in dict.fromkeys(u for u in (e.http_url, mirror_url(e.http_url), e.access_url) if u):
                    try:  # the bucket first, then the archive's own copy
                        wmap = wave_map_from_file(url)
                        self.store.put_wave_map(key, wmap)
                        break
                    except Exception:
                        wmap = None
            self._wave[key] = wmap
            return wmap

    # ------------------------------------------------------------------ jobs
    def start(self, grid: Grid, client: str | None = None, capped: bool = True) -> Job:
        """Start (or reuse) a background job that gathers every frame of a view.

        ``client`` is who asks (default: the caller of the request being
        answered). A caller with too many views in the works gets
        ``guard.Busy``; a view that exists already is always given."""
        vk = grid.key()
        client = guard.current() if client is None else client
        with self.lock:
            jid = self.by_view.get(vk)
            if jid and self.jobs[jid].status != "error":
                self.jobs[jid].last_used = time.time()
                job = self.jobs[jid]
            else:
                job = None
                if capped and client and not guard.internal(client):
                    active = [j for j in self.jobs.values() if j.status not in ("ready", "error")]
                    if sum(1 for j in active if j.client == client) >= settings.get("max_jobs_per_client"):
                        raise guard.Busy("too many views are being gathered for your address; wait for them to finish", 10.0)
                    if len(active) >= settings.get("max_jobs"):
                        raise guard.Busy("the server is gathering as many views as it can; try again in a moment", 10.0, 503)
                new = Job(id=uuid.uuid4().hex[:12], client=client or "")
                self.jobs[new.id] = new
                self.by_view[vk] = new.id
        if job is not None:
            self.store.touch(vk)
            return job
        threading.Thread(target=self._run, args=(new, grid), daemon=True).start()
        return new

    def peek(self, grid: Grid) -> Job | None:
        """The job of a view if it is in memory (never starts one)."""
        with self.lock:
            jid = self.by_view.get(grid.key())
            return self.jobs.get(jid) if jid else None

    def _forget(self, keep: Job) -> None:
        """Keep the frames held in memory under the budget: forget the views
        nobody opened for the longest time (their frames stay on disk)."""
        with self.lock:
            done = sorted((j for j in self.jobs.values() if j.status in ("ready", "error")), key=lambda j: j.last_used)
            held = {j.id: j.held() for j in self.jobs.values()}
            ram, disk = sum(h[0] for h in held.values()), sum(h[1] for h in held.values())
            for old in done:
                if (ram <= settings.get("ram_budget_mb") * 1e6 and disk <= config.SPILL_BUDGET_GB * 1e9
                        and len(self.jobs) <= 500):
                    break
                if old is keep:
                    continue
                ram, disk = ram - held[old.id][0], disk - held[old.id][1]
                self.jobs.pop(old.id, None)
                self.by_view = {k: v for k, v in self.by_view.items() if v != old.id}

    def _run(self, job: Job, grid: Grid) -> None:
        try:
            job.status = "searching"
            self.store.touch(grid.key())
            t0 = time.time()
            exps = self.view_exposures(grid)
            job.stats["search_s"] = time.time() - t0
            if len(exps) > config.MAX_FRAMES:  # deep fields: thin evenly in time, keep first and last
                step = (len(exps) - 1) / (config.MAX_FRAMES - 1)
                exps = [exps[round(k * step)] for k in range(config.MAX_FRAMES)]
            job.total = len(exps)
            if config.SPILL_NPIX and grid.npix > config.SPILL_NPIX:
                job.spill = Spill.open(len(exps), grid.npix)
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
                        job.keep(got)
            job.resuming = False
            if todo:
                # wavelength maps: one per release+detector, fetched once ever
                t0 = time.time()
                firsts = {f"{e.collection}_D{e.detector}": e for _, e in todo}.values()
                threads = [threading.Thread(target=self.wave_map, args=(e,), daemon=True) for e in firsts]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                items = [(k, e, self.wave_map(e)) for k, e in todo]
                job.stats["wave_s"] = time.time() - t0
                t0 = time.time()
                if config.FETCH_PROCS > 1:
                    self._fetch_in_processes(job, grid, items)
                else:
                    with self._one_core(), worker.Meter() as m:
                        asyncio.run(self._fetch_in_loop(job, grid, items))
                    job.stats.update(m.stats)
                job.stats["fetch_s"] = time.time() - t0
            job.status = "ready"
            if todo:  # after "ready": nobody waits for the housekeeping
                self.store.trim(config.DISK_BUDGET_GB * 1e9, keep={grid.key()} | self.pinned)
        except Exception as exc:  # surfaced to the UI
            job.status = "error"
            job.error = say(exc)
            log.warning("view %s failed: %s: %s", grid.key(), type(exc).__name__, exc)
        finally:
            job.resuming = False
            job.finished = time.time()
            self._forget(keep=job)

    def _collect(self, job: Job, grid: Grid, results: list[worker.Result]) -> None:
        self.store.put_frames(grid, [(k, e, f) for k, e, f, err in results if not err])
        for _, _, f, err in results:
            if err:
                job.failed += 1
            elif f is not None:
                job.keep(f)
            job.done += 1

    def _pool(self) -> ProcessPoolExecutor:
        with self.lock:
            if self._procs is not None and getattr(self._procs, "_broken", False):
                # a worker died (out of memory, killed): the pool refuses all work from then on
                self._procs.shutdown(wait=False, cancel_futures=True)
                self._procs = None
            if self._procs is None:
                per_proc = max(4, config.FETCH_WORKERS // config.FETCH_PROCS)
                self._procs = ProcessPoolExecutor(
                    max_workers=config.FETCH_PROCS,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=worker.init,
                    initargs=(per_proc,),
                )
            return self._procs

    def warm_up(self) -> None:
        """Start the worker processes now (each takes a few seconds to import
        astropy), so the first view after a restart does not wait for them."""
        if config.FETCH_PROCS > 1:
            pool = self._pool()
            for fut in [pool.submit(worker.ready, 0.5) for _ in range(config.FETCH_PROCS)]:
                fut.result()
        self.store.count_views()

    def keep_ready(self, grids: list[Grid]) -> None:
        """Have these views on disk (the featured targets): they are pinned
        there, so they still open when the archive or the bucket is away.
        One after another, and only what is missing is fetched."""
        for g in grids:
            self.pinned.add(g.key())
        for g in grids:
            try:
                self.frames_blocking(g, timeout=300)
            except Exception:
                pass  # tried again at the next start

    def _fetch_in_processes(self, job: Job, grid: Grid, items: list[worker.Item]) -> None:
        """Several cores: chunks of exposures are cut in worker processes,
        which this view shares with the others being gathered (see Scheduler)."""
        chunk = worker.slots(grid, config.FETCH_PROCS)  # cut at once in a worker process: fewer for a wide view
        chunks = [items[i : i + chunk] for i in range(0, len(items), chunk)]
        if len(chunks) > 1:  # a small first chunk: first frames on screen sooner
            head = max(1, min(4, len(chunks[0]) // 2))
            chunks = [chunks[0][:head], chunks[0][head:]] + chunks[1:]
        job.place = lambda: self._sched.place(job.id)
        try:
            for results, stats in self._sched.run(job.id, job.client, grid, chunks, spilled=job.spill is not None):
                for k, v in stats.items():
                    job.stats[k] = job.stats.get(k, 0) + v
                t0 = time.time()
                self._collect(job, grid, results)
                job.stats["store_s"] = job.stats.get("store_s", 0) + time.time() - t0
        finally:
            job.place = None

    @contextlib.contextmanager
    def _one_core(self):
        """One core: no more than ``active_views`` views are cut at a time (the
        Scheduler does that with several), each in its share of CUT_MB; the
        others wait for their turn."""
        with self._in_loop_turn:
            while self._in_loop >= self._loop_views():
                self._in_loop_turn.wait(1.0)  # a change of the setting is seen within a second
            self._in_loop += 1
        try:
            yield
        finally:
            with self._in_loop_turn:
                self._in_loop -= 1
                self._in_loop_turn.notify_all()

    @staticmethod
    def _loop_views() -> int:
        views = settings.get("active_views")
        return views if views > 0 else 1 << 30

    async def _fetch_in_loop(self, job: Job, grid: Grid, items: list[worker.Item]) -> None:
        """One core: everything in this process, frames arrive one by one."""
        views = self._loop_views()
        sem = asyncio.Semaphore(worker.slots(grid, views if views < 1 << 30 else 1))
        limits = httpx.Limits(max_connections=config.FETCH_WORKERS * 2, max_keepalive_connections=config.FETCH_WORKERS * 2)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), limits=limits) as client:

            async def one(key: str, e: Exposure, wmap) -> None:
                async with sem:
                    self._collect(job, grid, [await worker.cut_one(client, key, e, grid, wmap)])

            await asyncio.gather(*(one(*it) for it in items))

    def frames_blocking(self, grid: Grid, timeout: float = 600) -> list[Frame]:
        job = self.start(grid, client="")
        t0 = time.time()
        while job.status not in ("ready", "error") and time.time() - t0 < timeout:
            time.sleep(0.2)
        if job.status != "ready":
            raise RuntimeError(job.error or "timed out")
        return job.snapshot()
