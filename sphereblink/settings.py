"""Limits that are changed while the server runs.

How many jobs the pipeline works on at a time, how much memory their frames
may take and how much one caller may ask for are numbers somebody wants to
turn during a busy hour, and a deploy to change one takes the site down for
half a minute. So they live in a table, ``settings`` of the team database
(sql/settings.sql, docs/deployment.md "Settings in the database"), and the
server reads that table every ``REFRESH_S`` seconds: a changed row is in
force within the minute, without a restart.

The table cannot take the server down:

* a value is only taken inside the range written here (``Setting.lo`` to
  ``Setting.hi``); one outside it is moved to the nearest end, one that is
  not a number is ignored;
* the three settings that fill the memory are held together to what the
  machine was sized for (``memory_cap``);
* a setting without a row has the value of ``config`` (the environment
  variable, or its default in the code), exactly as before the table;
* when the database does not answer, the values read last stay in force;
  after a start without the database, those of ``config``. Nothing waits for
  the database: the table is read in a thread of its own, and ``get`` only
  looks in memory.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass

from . import config

log = logging.getLogger(__name__)

REFRESH_S = max(5.0, float(os.environ.get("SPHEREX_SETTINGS_REFRESH_S", "30")))
PROC_MB = 250.0  # what a measuring process holds (docs/performance.md, "How many jobs at once")


@dataclass(frozen=True)
class Setting:
    key: str    # the row of the table
    attr: str   # the value without a row: this name in config
    lo: float   # the range a row may set
    hi: float
    kind: type  # int, float or bool (a row holds a number: 0 or 1 for a bool)
    note: str

    @property
    def default(self):
        return getattr(config, self.attr)


SETTINGS: dict[str, Setting] = {s.key: s for s in (
    # the pipeline queue (service.Scheduler, measure_api)
    Setting("active_views", "ACTIVE_VIEWS", 1, 8, int,
            "Views whose exposures are cut at the same time; the next ones wait in the queue"),
    Setting("active_mb", "ACTIVE_MB", 300, 4000, float,
            "Memory (MB) the views being cut may fill with frames together; a wide field that does not fit waits"),
    Setting("measure_procs", "MEASURE_PROCS", 1, 4, int,
            "Measurements running at the same time; the next ones wait in the queue"),
    Setting("queue_preview", "QUEUE_PREVIEW", 0, 1, bool,
            "1: a view in the queue gets its first four exposures at once; 0: nothing before its turn"),
    Setting("ram_budget_mb", "RAM_BUDGET_MB", 300, 4000, float,
            "Memory (MB) for the frames of views already gathered; above it the views opened longest ago are reloaded from disk"),
    # how much may wait
    Setting("max_jobs", "MAX_JOBS", 8, 400, int,
            "Views gathered or waiting at once, all callers together; more are refused (503)"),
    Setting("max_jobs_per_client", "MAX_JOBS_PER_CLIENT", 4, 96, int,
            "Views gathered or waiting at once for one caller; more are refused (429)"),
    Setting("max_fields_per_client", "MAX_FIELDS_PER_CLIENT", 1, 32, int,
            "Wide fields loading at once for one caller"),
    Setting("max_measure_queue", "MAX_MEASURE_QUEUE", 2, 48, int,
            "Measurements waiting or running, all callers together; more are refused (503)"),
    Setting("max_open_requests", "MAX_OPEN_REQUESTS", 8, 128, int,
            "API calls being answered at once for one caller"),
    # how much one caller may ask for (guard.Buckets)
    Setting("rate_requests_per_s", "RATE_REQUESTS_PER_S", 5, 400, float, "API calls per second for one caller, sustained"),
    Setting("rate_requests_burst", "RATE_REQUESTS_BURST", 100, 12000, float, "API calls one caller may make at once"),
    Setting("rate_names_per_min", "RATE_NAMES_PER_MIN", 5, 600, float, "Names per minute one caller may send to the name services"),
    Setting("rate_catalogs_per_min", "RATE_CATALOGS_PER_MIN", 5, 600, float,
            "Catalogue and archive queries per minute that go out for one caller"),
    Setting("rate_tiles_per_s", "RATE_TILES_PER_S", 10, 1000, float, "Tiles of the sky photograph per second for one caller, sustained"),
    Setting("rate_tiles_burst", "RATE_TILES_BURST", 200, 30000, float, "Tiles of the sky photograph one caller may ask for at once"),
    Setting("rate_atlas_per_min", "RATE_ATLAS_PER_MIN", 60, 6000, float,
            "Sky tiles per minute the server fetches from the archive for one caller"),
    Setting("rate_measures_per_hour", "RATE_MEASURES_PER_HOUR", 1, 600, float, "New measurements per hour for one caller, sustained"),
    Setting("rate_measures_burst", "RATE_MEASURES_BURST", 1, 60, float, "New measurements one caller may start at once"),
)}
MEMORY = ("ram_budget_mb", "active_mb", "measure_procs")  # what fills the memory, see memory_cap

_lock = threading.Lock()
_over: dict[str, float] = {}     # the values the table puts in force; the others are those of config
_rows: dict[str, object] = {}    # the table as read last
_notes: dict[str, str] = {}      # why a value in force is not the one of its row
_read_at = 0.0                   # when the table was read last
_problem = ""                    # why the last reading failed, if it did
_listeners: list = []
_thread: threading.Thread | None = None


def get(key: str):
    """The value in force. Never waits."""
    s = SETTINGS[key]
    v = _over.get(key)
    if v is None:
        return s.default
    return bool(v) if s.kind is bool else int(v) if s.kind is int else float(v)


def on_change(listener) -> None:
    """Call ``listener()`` after a reading of the table that changed a value."""
    if listener not in _listeners:
        _listeners.append(listener)


def memory_cap() -> float:
    """MB the frames kept, the frames being gathered and the measuring
    processes may take together: what the values of ``config`` come to. Those
    are sized to the machine (render.yaml; docs/performance.md has the sum for
    production: 2000 + 2400 + 2 x 250, a worst case of 7.0 of its 8 GB), so
    the table can move memory between the three and never add any."""
    ram, active, procs = (SETTINGS[k].default for k in MEMORY)
    if ram <= 0 or active <= 0:  # config sets no limit: whoever runs this server chose that
        return math.inf
    return float(ram) + float(active) + PROC_MB * procs


def effective(rows: dict) -> tuple[dict[str, float], dict[str, str]]:
    """What a table sets: (the values it puts in force, notes on those that
    are not the ones written in it)."""
    over: dict[str, float] = {}
    notes: dict[str, str] = {}
    for key, raw in rows.items():
        s = SETTINGS.get(key)
        if s is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = math.nan
        if not math.isfinite(v):
            notes[key] = "not a number: the default is used"
            continue
        if s.kind is not float:
            v = float(round(v))
        took = min(max(v, s.lo), s.hi)
        if took != v:
            notes[key] = f"outside {s.lo:g} to {s.hi:g}: {took:g} is used"
        over[key] = took
    if any(k in over for k in MEMORY):
        now = {k: float(over.get(k, SETTINGS[k].default)) for k in MEMORY}
        cap = memory_cap()
        excess = now["ram_budget_mb"] + now["active_mb"] + PROC_MB * now["measure_procs"] - cap
        for key in ("ram_budget_mb", "active_mb"):  # the frames kept go first: a view forgotten is only read from disk again
            cut = min(excess, now[key] - SETTINGS[key].lo)
            if cut > 0:
                over[key] = now[key] - cut
                excess -= cut
                notes[key] = (f"lowered to {over[key]:g}: the frames kept, the frames being gathered and "
                              f"{PROC_MB:g} MB for each measuring process may take {cap:g} MB together")
    return over, notes


def apply(rows: dict) -> bool:
    """Put a table in force. Returns whether a value changed."""
    global _over, _rows, _notes
    over, notes = effective(rows)
    with _lock:
        before, said = {k: get(k) for k in SETTINGS}, _notes
        _over, _rows, _notes = over, dict(rows), notes
        changed = {k: (before[k], get(k)) for k in SETTINGS if get(k) != before[k]}
    for key, (old, new) in changed.items():
        log.warning("settings: %s is now %s (was %s)", key, new, old)
    for key, note in notes.items():
        if said.get(key) != note:  # once, not at every reading
            log.warning("settings: %s = %r in the table, %s", key, rows.get(key), note)
    if changed:
        for listener in list(_listeners):
            try:
                listener()
            except Exception:  # pragma: no cover - a listener must not stop the others
                log.exception("settings: a listener failed")
    return bool(changed)


def _read() -> dict:
    import psycopg  # imported here so the service runs without the driver when no database is set

    # tcp_user_timeout: a connection that went silent is given up after 10 s instead of holding this thread for good
    with psycopg.connect(config.DB_URL, connect_timeout=5, prepare_threshold=None, tcp_user_timeout=10000) as conn:
        conn.execute("set local statement_timeout = 5000")
        rows = conn.execute("select key, value from settings").fetchall()
    # (a database without an encoding hands text over as bytes)
    return {k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k): v for k, v in rows}


def refresh() -> bool:
    """Read the table once and put it in force. False when it could not be
    read: what is in force then stays."""
    global _read_at, _problem
    try:
        rows = _read()
    except Exception as exc:  # no database, no table, no network: none of them may reach a visitor
        problem = type(exc).__name__  # the name only: the text may carry the address of the database
        if problem != _problem:
            log.warning("settings: the table could not be read (%s); %s", problem,
                        "the values read last stay in force" if _read_at else "the values of the environment are in force")
        _problem = problem
        return False
    if _problem or not _read_at:
        log.warning("settings: read from the database (%d rows)", len(rows))
    _problem, _read_at = "", time.time()
    apply(rows)
    return True


def start(stop: threading.Event) -> None:
    """Read the table now and every ``REFRESH_S`` from here on, in a thread
    of its own. Without a database there is nothing to read."""
    global _thread
    if not config.DB_URL or _thread is not None:
        return

    def loop() -> None:
        while not stop.is_set():
            refresh()
            stop.wait(REFRESH_S)

    _thread = threading.Thread(target=loop, daemon=True, name="settings")
    _thread.start()


def status() -> dict:
    """Every setting with the value in force and where it comes from (``/api/settings``)."""
    with _lock:
        rows, notes, read_at, problem = dict(_rows), dict(_notes), _read_at, _problem
        values = {k: get(k) for k in SETTINGS}
    cast = lambda s, v: bool(v) if s.kind is bool else int(v) if s.kind is int else float(v)  # noqa: E731
    return {
        "database": bool(config.DB_URL),
        "read_seconds_ago": round(time.time() - read_at, 1) if read_at else None,
        "problem": problem or None,
        "refresh_s": REFRESH_S,
        "memory_cap_mb": None if math.isinf(memory_cap()) else memory_cap(),
        "settings": {
            key: {
                "value": values[key],
                "from": "corrected" if key in notes else "database" if key in rows else "default",
                "default": s.default,
                "min": cast(s, s.lo),
                "max": cast(s, s.hi),
                "note": notes.get(key),
                "what": s.note,
            }
            for key, s in SETTINGS.items()
        },
        "unknown_rows": sorted(str(k) for k in rows if k not in SETTINGS),
    }
