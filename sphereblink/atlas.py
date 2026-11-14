"""The photograph behind the sky map, kept on our own disk.

When the sky map is zoomed in, the viewer lays a photograph of the real sky
under the drawn stars: tiles of the 2MASS colour atlas (J, H and K bands) in
the HiPS format, made by CDS and served by the NASA/IPAC Infrared Science
Archive (IRSA). HiPS cuts the sky into the diamonds of the HEALPix grid:
order k has 12 * 4^k tiles, each a 512 pixel JPEG of about 100 KB.

The browsers used to fetch every tile from the archive themselves. Now they
ask this server, which keeps what it has fetched:

* a tile that is on disk is answered from disk, whatever the archive does;
* a tile that is not is fetched from the archive once, stored and answered.
  Several callers who want the same tile at the same moment share one fetch;
* the low orders (``ATLAS_KEEP_ORDER`` and below: the whole sky at a coarse
  scale) are never dropped, and ``seed`` fetches all of them after a start,
  so the first look at any part of the sky needs no archive at all;
* the deeper tiles are kept as long as there is room: above ``budget`` bytes
  the ones nobody asked for the longest time are deleted.

The whole atlas is not copied and must not be: it is about 445 GB (measured
on the archive, November 2026), and its publisher marks it ``unclonable``.
Orders 0 to 5 together are 1.65 GB.

The tiles are stored as the archive sends them, byte for byte, in the layout
of HiPS (``Norder3/Dir0/Npix300.jpg``). There is no database: the files are
the cache, and the time a file was last asked for is its modification time.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("uvicorn.error")

MAX_ORDER = 9  # the deepest order of the atlas
TILE_MAX_BYTES = 2_000_000  # the largest tiles are about 0.3 MB
FETCH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
REST_AFTER = 5  # failed fetches in a row before the archive is left alone ...
REST_S = 30.0  # ... for this long: a tile that is not on disk is refused at once meanwhile
MIN_FREE_BYTES = 1e9  # with less than this free on the disk no new tile is stored (the disk is shared)
TOUCH_EVERY_S = 6 * 3600.0  # how often a tile's "last asked for" is written to disk
CREDIT = ("2MASS colour atlas (J, H, K): University of Massachusetts and IPAC/Caltech, funded by NASA and NSF; "
          "HiPS by CDS (CNRS/Unistra, ODbL-1.0), from the NASA/IPAC Infrared Science Archive")


class NotThere(Exception):
    """The atlas has no such tile."""


class Away(Exception):
    """The archive did not give the tile (down, slow, or answering with something else)."""


def tiles_in(order: int) -> int:
    return 12 * 4 ** order


def address(order: int, pix: int) -> str:
    """Where HiPS keeps a tile, under the atlas' base address."""
    return f"Norder{order}/Dir{pix // 10000 * 10000}/Npix{pix}.jpg"


def is_jpeg(data: bytes) -> bool:
    return 1000 < len(data) <= TILE_MAX_BYTES and data[:3] == b"\xff\xd8\xff"


class _Flight:
    """One fetch of a tile, which everybody who asks for that tile meanwhile waits for."""

    def __init__(self):
        self.done = threading.Event()
        self.data: bytes | None = None
        self.error: Exception | None = None


class Atlas:
    def __init__(self, root: Path | str | None = None, upstream: str | None = None, budget_bytes: float | None = None,
                 keep_order: int | None = None, client: httpx.Client | None = None, fetches: int | None = None):
        self.root = Path(root) if root is not None else config.CACHE_DIR / "atlas" / "2mass"
        self.upstream = (upstream or config.ATLAS_URL).rstrip("/")
        self.budget = float(config.ATLAS_BUDGET_GB * 1e9 if budget_bytes is None else budget_bytes)
        self.keep_order = config.ATLAS_KEEP_ORDER if keep_order is None else keep_order
        self._client = client
        self._gate = threading.BoundedSemaphore(fetches or config.ATLAS_FETCHES)  # fetches from the archive at once
        self._lock = threading.Lock()
        self._index: dict[tuple[int, int], list[float]] = {}  # (order, pix) -> [bytes, last asked for, as written to disk]
        self._bytes = 0.0
        self._scanned = threading.Event()
        self._flights: dict[tuple[int, int], _Flight] = {}
        self._missing: dict[tuple[int, int], float] = {}  # tiles the archive said it does not have, and until when we believe it
        self._fails = 0
        self._rest_until = 0.0
        self.hits = self.fetched = self.dropped = 0
        self._free = (0.0, float("inf"))  # when the free space of the disk was read, and what it was
        self.seeding: dict | None = None

    # ------------------------------------------------------------------ the tiles on disk
    def path(self, order: int, pix: int) -> Path:
        return self.root / address(order, pix)

    def scan(self) -> None:
        """Read what is on disk (once, after a start): sizes and when each tile
        was last asked for. Tiles stored while this runs are already counted."""
        found = 0
        if self.root.is_dir():
            for odir in os.scandir(self.root):
                if not (odir.is_dir() and odir.name.startswith("Norder") and odir.name[6:].isdigit()):
                    continue
                order = int(odir.name[6:])
                for ddir in os.scandir(odir.path):
                    if not ddir.is_dir():
                        continue
                    for f in os.scandir(ddir.path):
                        name = f.name
                        if name.endswith(".part"):  # a tile that was being written when the server stopped
                            try:
                                os.unlink(f.path)
                            except OSError:
                                pass
                            continue
                        if not (name.startswith("Npix") and name.endswith(".jpg") and name[4:-4].isdigit()):
                            continue
                        try:
                            st = f.stat()
                        except OSError:
                            continue
                        with self._lock:
                            key = (order, int(name[4:-4]))
                            if key not in self._index:
                                self._index[key] = [st.st_size, st.st_mtime, st.st_mtime]
                                self._bytes += st.st_size
                                found += 1
        self._scanned.set()
        self._trim()
        self.free_bytes()
        log.info("atlas: %d tiles on disk, %.2f GB of %.2f GB", found, self._bytes / 1e9, self.budget / 1e9)

    def _used(self, key: tuple[int, int], path: Path) -> None:
        now = time.time()
        with self._lock:
            e = self._index.get(key)
            if e is None:
                return
            e[1] = now
            if now - e[2] < TOUCH_EVERY_S:
                return
            e[2] = now
        try:
            os.utime(path)
        except OSError:
            pass

    def free_bytes(self) -> float:
        """Free space of the disk the tiles are on (read at most every 30 s)."""
        now = time.time()
        if now - self._free[0] > 30.0:
            try:
                self._free = (now, float(shutil.disk_usage(self.root if self.root.is_dir() else self.root.parent.parent).free))
            except OSError:
                self._free = (now, float("inf"))
        return self._free[1]

    def _store(self, key: tuple[int, int], path: Path, data: bytes) -> None:
        if self.free_bytes() < MIN_FREE_BYTES:
            # The disk also holds the frame cache. Whatever fills it, the
            # tiles do not make it worse: this one is shown and not kept.
            if self._free[0] != getattr(self, "_warned", 0.0):
                self._warned = self._free[0]
                log.warning("atlas: %.2f GB free on the disk, new tiles are not stored", self._free[1] / 1e9)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.part")
        part.write_bytes(data)
        os.replace(part, path)  # a reader sees the whole tile or none of it
        now = time.time()
        with self._lock:
            old = self._index.get(key)
            self._bytes += len(data) - (old[0] if old else 0)
            self._index[key] = [len(data), now, now]
            self.fetched += 1
        self._trim()

    def _trim(self) -> None:
        """Above the budget, delete the deep tiles nobody asked for the longest
        time, down to 90 % of it. The orders that are kept are never touched."""
        if not self._scanned.is_set():
            return  # what is on disk has not been counted yet
        with self._lock:
            if self._bytes <= self.budget:
                return
            deep = sorted((e[1], key) for key, e in self._index.items() if key[0] > self.keep_order)
            victims = []
            for _, key in deep:
                if self._bytes <= 0.9 * self.budget:
                    break
                self._bytes -= self._index.pop(key)[0]
                victims.append(key)
            self.dropped += len(victims)
        for key in victims:
            try:
                os.unlink(self.path(*key))
            except OSError:
                pass
        if victims:
            log.info("atlas: dropped %d tiles not asked for the longest, %.2f GB kept", len(victims), self._bytes / 1e9)

    # ------------------------------------------------------------------ a tile
    def get(self, order: int, pix: int, charge=None) -> tuple[bytes, bool]:
        """The JPEG of a tile and whether it came from disk. ``charge`` is
        called before a fetch from the archive goes out (the caller's
        allowance; it may raise). Raises NotThere or Away."""
        if not (0 <= order <= MAX_ORDER and 0 <= pix < tiles_in(order)):
            raise NotThere("no such tile")
        key, path = (order, pix), self.path(order, pix)
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        if data is not None:
            self.hits += 1
            self._used(key, path)
            return data, True
        if charge is not None:
            charge()
        return self._fetch(key, path), False

    def _fetch(self, key: tuple[int, int], path: Path) -> bytes:
        with self._lock:
            flight = self._flights.get(key)
            mine = flight is None
            if mine:
                flight = self._flights[key] = _Flight()
        if not mine:  # somebody is fetching this tile right now: take what they get
            flight.done.wait(40.0)
            if flight.data is not None:
                return flight.data
            raise flight.error or Away("the archive is slow")
        try:
            flight.data = self._download(key)
            self._store(key, path, flight.data)
            return flight.data
        except Exception as exc:
            flight.error = exc if isinstance(exc, (NotThere, Away)) else Away(type(exc).__name__)
            if flight.data is not None:  # fetched, but the disk refused it: the tile is still good to show
                log.warning("atlas: could not store %s: %r", address(*key), exc)
                return flight.data
            if flight.error is exc:
                raise
            raise flight.error from exc
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(
                        timeout=FETCH_TIMEOUT, limits=httpx.Limits(max_connections=16, max_keepalive_connections=12),
                        headers={"User-Agent": "VanillaSphere/0.2 (sky map tile cache; https://vanillasphere.com)"})
        return self._client

    def _download(self, key: tuple[int, int]) -> bytes:
        now = time.time()
        if self._missing.get(key, 0.0) > now:
            raise NotThere("the atlas has no such tile")
        if now < self._rest_until:
            raise Away("the archive does not answer; it is left alone for a moment")
        if not self._gate.acquire(timeout=20.0):
            raise Away("too many tiles are being fetched")
        try:
            r = self._http().get(f"{self.upstream}/{address(*key)}")
        except httpx.HTTPError as exc:
            self._failed()
            raise Away(f"the archive did not answer ({type(exc).__name__})") from exc
        finally:
            self._gate.release()
        if r.status_code == 404:
            if len(self._missing) > 5000:
                self._missing.clear()
            self._missing[key] = now + 3600.0
            raise NotThere("the atlas has no such tile")
        if r.status_code != 200 or not is_jpeg(r.content):
            self._failed()
            raise Away(f"the archive answered {r.status_code} with something that is not a tile")
        self._fails = 0
        return r.content

    def _failed(self) -> None:
        with self._lock:
            self._fails += 1
            if self._fails >= REST_AFTER:
                self._fails, self._rest_until = 0, time.time() + REST_S
                log.warning("atlas: the archive failed %d times in a row; leaving it alone for %d s", REST_AFTER, REST_S)

    # ------------------------------------------------------------------ the orders that are kept
    def seed(self, stop: threading.Event, workers: int = 4, passes: int = 30) -> None:
        """Fetch every tile of the kept orders that is not on disk yet, the
        coarse orders first, a few at a time so the archive hardly notices.
        What fails is tried again a minute later; a restart carries on where
        the last one stopped."""
        self._scanned.wait()

        def one(key: tuple[int, int]) -> bool:
            try:
                self._fetch(key, self.path(*key))
                return True
            except (NotThere, Away):
                return False

        t0, got, halt = time.time(), 0, False  # halt: stop for good (the server is closing, or the disk has no room)
        with ThreadPoolExecutor(workers, thread_name_prefix="atlas-seed") as pool:
            for attempt in range(passes):
                with self._lock:
                    todo = [(o, p) for o in range(self.keep_order + 1) for p in range(tiles_in(o)) if (o, p) not in self._index]
                if not todo or (attempt and stop.wait(60.0)):
                    break
                for i in range(0, len(todo), 32):
                    if stop.is_set() or self.free_bytes() < MIN_FREE_BYTES:  # a full disk keeps nothing: do not fetch for it
                        halt = True
                        break
                    batch = todo[i:i + 32]
                    self.seeding = {"order": batch[0][0], "left": len(todo) - i}
                    ok = sum(pool.map(one, batch))
                    got += ok
                    if 2 * ok < len(batch) and stop.wait(REST_S):  # the archive is having a bad time
                        halt = True
                        break
                if halt:
                    break
        self.seeding = None
        if got:
            log.info("atlas: fetched %d tiles of orders 0-%d in %.0f s", got, self.keep_order, time.time() - t0)

    def start(self, stop: threading.Event, seed: bool) -> None:
        """Count what is on disk and, if asked, fill the kept orders, in the background."""
        def run() -> None:
            try:
                self.scan()
                if seed:
                    self.seed(stop)
            except Exception:
                log.exception("atlas: background work stopped")

        threading.Thread(target=run, daemon=True, name="atlas").start()

    def stats(self) -> dict:
        with self._lock:
            kept = sum(1 for o, _ in self._index if o <= self.keep_order)
            return {
                "upstream": self.upstream,
                "credit": CREDIT,
                "tiles": len(self._index),
                "bytes": int(self._bytes),
                "budget_bytes": int(self.budget),
                "disk_free_bytes": None if self._free[1] == float("inf") else int(self._free[1]),
                "kept_orders": [0, self.keep_order],
                "kept_tiles": kept,
                "kept_tiles_wanted": sum(tiles_in(o) for o in range(self.keep_order + 1)),
                "counted": self._scanned.is_set(),
                "seeding": self.seeding,
                "from_disk": self.hits,
                "fetched": self.fetched,
                "dropped": self.dropped,
                "archive_resting": time.time() < self._rest_until,
            }
