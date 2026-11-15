"""How much memory the service really holds.

The host's memory graph counts the file cache with the memory in use: every
frame written to the cache on disk stays in that figure until the kernel
needs the room, so the graph can stand near the limit while most of it could
be given back at once. What can kill the service is the rest, the memory
that cannot be given back. ``usage`` reads both from the container's own
accounting (cgroup v2), and how much each process of the service holds, so
that ``/api/health`` can say which of the two the graph is showing.

It also hands memory back (``keep_trimmed``). The C library keeps what a
process frees for that process's next request, inside its heap, and gives
the pages back to the system only from the heap's top. After a wide field the
web process and the processes that cut its exposures therefore went on
holding what they no longer used: hundreds of megabytes each, for as long as
they lived, so that a busy hour left the service near its limit when it was
idle again. ``malloc_trim`` gives the free pages inside the heap back; each
process of the service calls it now and then while it has been working.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("uvicorn.error")  # the server's own log, which the host keeps

CGROUP = Path("/sys/fs/cgroup")
PROC = Path("/proc")
# Seconds between two looks at whether this process should hand memory back (0: never).
TRIM_S = float(os.environ.get("SPHEREX_TRIM_S", "15"))
TRIM_AFTER_CPU_S = 0.1  # ... which it does when it has worked this long since the last time: an idle process has nothing new to give


def _mb(n: float) -> float:
    return round(n / 1e6, 1)


def _stat(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        try:
            out[name] = int(value)
        except ValueError:
            pass
    return out


def container(root: Path = CGROUP) -> dict | None:
    """The container's memory, in MB: its limit, what it holds that cannot be
    given back (``used_mb``), the file cache that can (``file_cache_mb``) and
    what it may still take (``free_mb``, the cache counted as free). None
    where there is no limit to read (a laptop, a container without one)."""
    try:
        limit = (root / "memory.max").read_text().strip()
        if limit == "max":
            return None
        limit, current = int(limit), int((root / "memory.current").read_text())
        stat = _stat((root / "memory.stat").read_text())
    except (OSError, ValueError):
        return None
    cache = stat.get("inactive_file", 0) + stat.get("active_file", 0)
    used = current - cache
    out = {
        "limit_mb": _mb(limit),
        "used_mb": _mb(used),
        "file_cache_mb": _mb(cache),
        "free_mb": _mb(limit - used),
        # "working set": the figure container platforms usually plot (the cache in recent use counts as used)
        "working_set_mb": _mb(current - stat.get("inactive_file", 0)),
    }
    for key in ("anon", "shmem", "kernel"):
        if key in stat:
            out[f"{key}_mb"] = _mb(stat[key])
    try:
        out["peak_mb"] = _mb(int((root / "memory.peak").read_text()))
    except (OSError, ValueError):
        pass
    try:  # times the kernel killed a process of this container for lack of memory, since the container started
        out["oom_kills"] = _stat((root / "memory.events").read_text()).get("oom_kill", 0)
    except OSError:
        pass
    return out


def free_mb(root: Path = CGROUP) -> float | None:
    """Memory this container may still take, counting the file cache it can
    give back as free. None where the container has no limit to read."""
    c = container(root)
    return None if c is None else c["free_mb"]


def _status(pid: int, proc: Path) -> dict[str, str]:
    out = {}
    for line in (proc / str(pid) / "status").read_text().splitlines():
        name, _, value = line.partition(":")
        out[name] = value.strip()
    return out


def _kb(value: str) -> int:
    return int(value.split()[0]) * 1024 if value else 0


def processes(root_pid: int | None = None, proc: Path = PROC) -> list[dict]:
    """The web process and the processes it started (fetching, measuring,
    the mover search), largest first: the memory each holds for itself
    (``own_mb``) and the shared memory it has mapped (``shared_mb``, which
    several of them may count)."""
    root_pid = root_pid or os.getpid()
    seen: dict[int, dict[str, str]] = {}
    try:
        pids = [int(p.name) for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return []
    for pid in pids:
        try:
            seen[pid] = _status(pid, proc)
        except (OSError, ValueError):
            continue  # it ended meanwhile
    family, grew = {root_pid}, True
    while grew:
        more = {pid for pid, s in seen.items() if pid not in family and s.get("PPid", "").isdigit() and int(s["PPid"]) in family}
        family |= more
        grew = bool(more)
    out = []
    for pid in family:
        s = seen.get(pid)
        if s is None:
            continue
        try:
            out.append({
                "role": "web" if pid == root_pid else "worker",
                "own_mb": _mb(_kb(s.get("RssAnon", ""))),
                "shared_mb": _mb(_kb(s.get("RssShmem", ""))),
            })
        except (ValueError, IndexError):
            continue
    return sorted(out, key=lambda p: -p["own_mb"])


def own_mb(proc: Path = PROC) -> float:
    """Memory this process holds for itself, in MB (0 where it cannot be read)."""
    try:
        return _mb(_kb(_status(os.getpid(), proc).get("RssAnon", "")))
    except (OSError, ValueError, IndexError):
        return 0.0


_libc = None
_trimmer: threading.Thread | None = None
trimmed = {"times": 0, "released_mb": 0.0, "slowest_s": 0.0}  # what this process handed back since it started


def trim() -> bool:
    """Give the pages this process has freed back to the system. True when
    there were some. Does nothing where the C library has no ``malloc_trim``
    (anything but glibc)."""
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL("libc.so.6")
            _libc.malloc_trim.argtypes, _libc.malloc_trim.restype = [ctypes.c_size_t], ctypes.c_int
        except (OSError, AttributeError):
            _libc = False
    if not _libc:
        return False
    before, t0 = own_mb(), time.perf_counter()
    gave = bool(_libc.malloc_trim(0))
    took = time.perf_counter() - t0
    if gave:
        trimmed["times"] += 1
        trimmed["released_mb"] = round(trimmed["released_mb"] + max(0.0, before - own_mb()), 1)
    trimmed["slowest_s"] = round(max(trimmed["slowest_s"], took), 3)
    if took > 1.0:  # it holds the heap's lock meanwhile: worth knowing if it ever takes long
        log.warning("memory: handing free memory back took %.1f s", took)
    return gave


def keep_trimmed(every: float | None = None, stop: threading.Event | None = None) -> None:
    """From now on, hand the memory this process has freed back to the
    system every ``every`` seconds in which it has worked (once per process;
    the pools start it in each of their processes). An idle process is left
    alone: it has freed nothing since the last time."""
    global _trimmer
    every = TRIM_S if every is None else every
    if every <= 0 or (_trimmer is not None and _trimmer.is_alive()):
        return

    def run() -> None:
        worked, again = time.process_time(), False
        while not (stop.wait(every) if stop is not None else time.sleep(every)):
            try:
                busy = time.process_time() - worked >= TRIM_AFTER_CPU_S
                # ... and once more after its work has ended: what the last job freed came after the last look
                if busy or again:
                    trim()
                    worked, again = time.process_time(), busy
            except Exception:  # pragma: no cover - never worth a process
                log.exception("memory: could not hand free memory back")
                return

    _trimmer = threading.Thread(target=run, daemon=True, name="memory-trim")
    _trimmer.start()


def usage() -> dict | None:
    """What ``/api/health`` shows. Never raises: the health check must answer."""
    try:
        c = container()
        if c is None:
            return None
        c["processes"] = processes()
        c["handed_back"] = dict(trimmed)  # by the web process, since it started
        return c
    except Exception:  # pragma: no cover - whatever the host's files hold, the health check answers
        return None
