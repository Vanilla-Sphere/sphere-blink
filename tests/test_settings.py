"""The limits kept in the table ``settings`` of the team database: what a row may set, what happens
to a bad one, what stays in force without the database, and that a change reaches the queue."""

import math
import os
import re
import threading
import time
from pathlib import Path

import pytest

from sphereblink import config, guard, measure_api, settings

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = {"ACTIVE_VIEWS": 4, "ACTIVE_MB": 2400.0, "MEASURE_PROCS": 2, "RAM_BUDGET_MB": 2000.0}  # render.yaml


@pytest.fixture(autouse=True)
def as_started(monkeypatch):
    """Every test starts and ends with nothing read from a table."""
    for name, empty in (("_over", {}), ("_rows", {}), ("_notes", {}), ("_read_at", 0.0), ("_problem", "")):
        monkeypatch.setattr(settings, name, empty)
    yield
    monkeypatch.undo()
    guard._rates_changed()


@pytest.fixture
def production(monkeypatch):
    for name, value in PRODUCTION.items():
        monkeypatch.setattr(config, name, value)


# ----------------------------------------------------------------------------- what a row may set
def test_without_a_row_a_setting_has_the_value_of_the_environment(monkeypatch):
    for s in settings.SETTINGS.values():  # every setting names a value of config, inside its own range
        assert s.lo <= s.default <= s.hi and s.lo < s.hi, s.key
    monkeypatch.setattr(config, "ACTIVE_VIEWS", 3)
    assert settings.get("active_views") == 3
    monkeypatch.setattr(config, "ACTIVE_VIEWS", 0)  # "no limit" stays possible there, where the operator writes it
    assert settings.get("active_views") == 0
    assert settings.status()["settings"]["active_views"]["from"] == "default"


def test_a_row_sets_the_value_and_its_kind(production):
    assert settings.apply({"active_views": 6, "active_mb": 1800, "queue_preview": 0, "rate_requests_per_s": 12.5})
    assert settings.get("active_views") == 6 and type(settings.get("active_views")) is int
    assert settings.get("active_mb") == 1800.0 and type(settings.get("active_mb")) is float
    assert settings.get("queue_preview") is False
    assert settings.get("rate_requests_per_s") == 12.5
    assert settings.get("measure_procs") == 2  # no row: unchanged
    st = settings.status()["settings"]
    assert st["active_views"] == {"value": 6, "from": "database", "default": 4, "min": 1, "max": 8, "note": None,
                                  "what": settings.SETTINGS["active_views"].note}
    assert not settings.apply({"active_views": 6, "active_mb": 1800, "queue_preview": 0, "rate_requests_per_s": 12.5})  # nothing new
    assert settings.apply({})  # the rows are deleted: back to the environment
    assert settings.get("active_views") == 4 and settings.get("queue_preview") is True


def test_a_value_outside_its_range_is_moved_to_the_nearest_end(production):
    settings.apply({"active_views": 500, "measure_procs": 0, "rate_requests_per_s": 0, "max_jobs": -3, "queue_preview": 7,
                    "active_mb": 1})
    assert settings.get("active_views") == 8 and settings.get("measure_procs") == 1
    assert settings.get("rate_requests_per_s") == 5.0  # 0 would turn the limit off, a tiny rate would lock everybody out
    assert settings.get("max_jobs") == 8 and settings.get("queue_preview") is True and settings.get("active_mb") == 300.0
    st = settings.status()["settings"]
    assert st["active_views"]["from"] == "corrected" and st["active_views"]["note"] == "outside 1 to 8: 8 is used"


def test_a_corrected_row_is_logged_once_not_at_every_reading(production, caplog):
    with caplog.at_level("WARNING", logger="sphereblink.settings"):
        for _ in range(3):
            settings.apply({"active_views": 500})
    assert [r.getMessage() for r in caplog.records] == [
        "settings: active_views is now 8 (was 4)", "settings: active_views = 500 in the table, outside 1 to 8: 8 is used"]


@pytest.mark.parametrize("bad", [None, "many", "", math.nan, math.inf, -math.inf, [4], {"v": 4}])
def test_a_value_that_is_not_a_number_is_ignored(production, bad):
    settings.apply({"active_views": bad, "measure_procs": 3})
    assert settings.get("active_views") == 4 and settings.get("measure_procs") == 3
    assert settings.status()["settings"]["active_views"]["note"] == "not a number: the default is used"


def test_numbers_as_text_and_fractions_are_read_and_unknown_rows_are_named(production):
    settings.apply({"active_views": "5", "measure_procs": 2.6, "actve_views": 9, 7: 1})
    assert settings.get("active_views") == 5 and settings.get("measure_procs") == 3
    assert settings.status()["unknown_rows"] == ["7", "actve_views"]


# ----------------------------------------------------------------------------- the memory
def test_the_three_settings_that_fill_the_memory_never_add_up_to_more_than_the_machine_was_sized_for(production):
    assert settings.memory_cap() == 2000 + 2400 + 2 * 250  # docs/performance.md: the worst case of 7.0 GB of 8
    settings.apply({"measure_procs": 4})  # two more measuring processes: the frames kept make room
    assert settings.get("measure_procs") == 4 and settings.get("ram_budget_mb") == 1500.0 and settings.get("active_mb") == 2400.0
    assert "4900 MB together" in settings.status()["settings"]["ram_budget_mb"]["note"]
    settings.apply({"active_mb": 4000, "ram_budget_mb": 4000, "measure_procs": 4})  # every one at its top
    used = settings.get("ram_budget_mb") + settings.get("active_mb") + 250 * settings.get("measure_procs")
    assert used == 4900 and settings.get("ram_budget_mb") == 300.0 and settings.get("active_mb") == 3600.0
    settings.apply({"active_mb": 3000})  # more for the views being gathered: taken from the frames kept
    assert settings.get("active_mb") == 3000.0 and settings.get("ram_budget_mb") == 1400.0
    settings.apply({"active_mb": 1200, "ram_budget_mb": 3200})  # memory moved from one to the other: nothing to correct
    assert settings.get("ram_budget_mb") == 3200.0 and not settings.status()["settings"]["ram_budget_mb"]["note"]
    # whatever the table holds
    import random

    rng = random.Random(7)
    for _ in range(300):
        rows = {k: rng.choice([rng.uniform(-1e4, 1e5), math.nan, "x", 0, 1e12]) for k in rng.sample(list(settings.SETTINGS), 6)}
        settings.apply(rows)
        assert settings.get("ram_budget_mb") + settings.get("active_mb") + 250 * settings.get("measure_procs") <= 4900
        assert all(s.lo <= settings.get(k) <= s.hi for k, s in settings.SETTINGS.items())


def test_an_environment_without_a_memory_limit_keeps_none(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_MB", 0.0)
    assert settings.memory_cap() == math.inf and settings.status()["memory_cap_mb"] is None
    settings.apply({"ram_budget_mb": 4000, "measure_procs": 4})
    assert settings.get("ram_budget_mb") == 4000.0


# ----------------------------------------------------------------------------- without the database
def test_a_database_that_does_not_answer_changes_nothing(production, monkeypatch, caplog):
    def down():
        raise OSError("connection to server at db.internal:6543 failed")

    monkeypatch.setattr(settings, "_read", down)
    with caplog.at_level("WARNING", logger="sphereblink.settings"):
        assert not settings.refresh() and not settings.refresh()
    assert settings.get("active_views") == 4  # after a start without it: the environment
    st = settings.status()
    assert st["problem"] == "OSError" and st["read_seconds_ago"] is None and "db.internal" not in str(st)
    assert len(caplog.records) == 1 and "db.internal" not in caplog.text  # said once, without the address

    monkeypatch.setattr(settings, "_read", lambda: {"active_views": 6})
    assert settings.refresh() and settings.get("active_views") == 6 and settings.status()["problem"] is None
    monkeypatch.setattr(settings, "_read", down)
    assert not settings.refresh()
    assert settings.get("active_views") == 6  # it answered before: what it said last stays in force
    assert settings.status()["problem"] == "OSError" and settings.status()["read_seconds_ago"] is not None


def test_no_database_no_reading(monkeypatch):
    monkeypatch.setattr(config, "DB_URL", "")
    monkeypatch.setattr(settings, "_thread", None)
    settings.start(threading.Event())
    assert settings._thread is None and settings.status()["database"] is False


def test_the_table_is_read_again_and_again_until_the_server_stops(monkeypatch):
    reads, stop = [], threading.Event()
    monkeypatch.setattr(config, "DB_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(settings, "_thread", None)
    monkeypatch.setattr(settings, "REFRESH_S", 0.02)
    monkeypatch.setattr(settings, "_read", lambda: reads.append(1) or {"max_jobs": 100 + len(reads)})
    settings.start(stop)
    thread = settings._thread
    settings.start(stop)  # a second start is the same thread
    assert settings._thread is thread
    for _ in range(200):
        if len(reads) >= 3:
            break
        time.sleep(0.01)
    stop.set()
    thread.join(2)
    assert len(reads) >= 3 and not thread.is_alive() and settings.get("max_jobs") == 100 + len(reads)


# ----------------------------------------------------------------------------- a change reaches the queue
def test_views_in_the_queue_have_their_turn_when_the_limit_goes_up(production, monkeypatch):
    from sphereblink.service import Scheduler

    s = Scheduler(lambda: None, 4)
    for k in range(7):
        s._views[f"v{k}"] = {"name": f"v{k}", "client": f"c{k}", "started": float(k), "pending": list(range(20)), "sent": 1,
                             "active": False, "mb": 9.0}
    monkeypatch.setattr(s, "_pump", lambda: None)
    settings.on_change(s.recheck)
    try:
        s.recheck()
        assert sum(v["active"] for v in s._views.values()) == 4 and s.place("v6") == (3, 3)
        settings.apply({"active_views": 6})
        assert sum(v["active"] for v in s._views.values()) == 6 and s.place("v6") == (1, 1)
        settings.apply({"active_views": 2})  # down: nobody loses a place, the next ones wait for two to finish
        assert sum(v["active"] for v in s._views.values()) == 6
        for name in ("v0", "v1", "v2", "v3"):
            del s._views[name]
            s._admit()
        assert [n for n, v in s._views.items() if v["active"]] == ["v4", "v5"] and s.place("v6") == (1, 1)
        del s._views["v4"]
        s._admit()
        assert s._views["v6"]["active"]
        del s._views["v5"]
        s._views["w"] = {"name": "w", "client": "w", "started": 99.0, "pending": [1], "sent": 0, "active": False, "mb": 9.0}
        settings.apply({"active_views": 1, "queue_preview": 0})
        assert s._next()["name"] == "v6"  # no first exposures for the one that waits
        settings.apply({"active_views": 1, "queue_preview": 1})
        assert s._next()["name"] == "w"
    finally:
        settings._listeners.remove(s.recheck)


def test_the_allowances_of_a_caller_follow_the_table(production, monkeypatch):
    monkeypatch.setattr(guard, "requests", guard.Buckets(config.RATE_REQUESTS_PER_S, config.RATE_REQUESTS_BURST))
    monkeypatch.setattr(guard, "measures", guard.Buckets(config.RATE_MEASURES_PER_HOUR / 3600.0, config.RATE_MEASURES_BURST))
    assert guard.requests.left("203.0.113.5") == 1200
    settings.apply({"rate_requests_burst": 100, "rate_requests_per_s": 5, "rate_measures_per_hour": 360, "rate_measures_burst": 2})
    assert (guard.requests.rate, guard.requests.burst) == (5.0, 100.0)
    assert guard.measures.rate == pytest.approx(0.1) and guard.measures.burst == 2.0
    assert guard.requests.left("203.0.113.5") == 100
    assert [guard.measures.take("203.0.113.5") == 0 for _ in range(3)] == [True, True, False]
    settings.apply({})
    assert (guard.requests.rate, guard.requests.burst) == (config.RATE_REQUESTS_PER_S, config.RATE_REQUESTS_BURST)


def test_requests_open_at_once_and_views_per_caller_follow_the_table(production, monkeypatch):
    from fastapi.testclient import TestClient

    from sphereblink import main
    from sphereblink.cutout import Grid

    client = TestClient(main.app)
    body = client.get("/api/settings")
    assert body.status_code == 200 and body.headers["cache-control"] == "no-store"
    assert set(body.json()["settings"]) == set(settings.SETTINGS) and body.json()["refresh_s"] == settings.REFRESH_S

    monkeypatch.setattr(main.svc, "_run", lambda job, grid: None)
    monkeypatch.setattr(main.svc, "jobs", {})
    monkeypatch.setattr(main.svc, "by_view", {})
    settings.apply({"max_jobs_per_client": 4})
    for k in range(4):
        main.svc.start(Grid.from_fov(20.0 + k, 5.0, 8.0), client="203.0.113.9")
    with pytest.raises(guard.Busy):
        main.svc.start(Grid.from_fov(30.0, 5.0, 8.0), client="203.0.113.9")
    settings.apply({"max_jobs_per_client": 5})
    main.svc.start(Grid.from_fov(30.0, 5.0, 8.0), client="203.0.113.9")


def test_measuring_processes_follow_the_table(production, monkeypatch):
    monkeypatch.setattr(measure_api, "_waiting", [])
    monkeypatch.setattr(measure_api, "_measuring", 0)
    monkeypatch.setattr(measure_api, "_most", 0)

    class Pool:
        closed = False

        def shutdown(self, wait=True):
            self.closed = True

    pool = Pool()
    monkeypatch.setattr(measure_api, "_pool", pool)
    measure_api._take_turn("a")
    measure_api._take_turn("b")
    third = threading.Thread(target=measure_api._take_turn, args=("c",), daemon=True)
    third.start()
    time.sleep(0.1)
    assert measure_api._measuring == 2 and measure_api.place("c") == (1, 1)  # two at a time: the third waits
    settings.apply({"measure_procs": 3})
    with measure_api._turn:
        measure_api._turn.notify_all()  # (the wait also looks again by itself every 5 s)
    third.join(2)
    assert measure_api._measuring == 3 and measure_api._most == 3 and measure_api.place("c") == (0, 0)
    settings.apply({"measure_procs": 2})
    for _ in range(2):
        measure_api._turn_over()
    assert not pool.closed and measure_api._pool is pool  # one is still measuring: its pool stays
    measure_api._turn_over()
    # nothing is measured and the pool had started a process more than the setting asks for now: it is let go
    assert pool.closed and measure_api._pool is None and measure_api._most == 0


# ----------------------------------------------------------------------------- the table itself
def seeded():
    """The rows of sql/settings.sql: key -> (value, min, max, description)."""
    text = (ROOT / "sql" / "settings.sql").read_text()
    rows = re.findall(r"^\s*\('(\w+)',\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*'((?:[^']|'')*)'\),?$", text, re.M)
    return {key: (float(value), float(lo), float(hi), note.replace("''", "'")) for key, value, lo, hi, note in rows}


def test_the_table_of_the_repository_is_the_list_of_the_code_with_the_values_of_production():
    yaml = pytest.importorskip("yaml")
    rows = seeded()
    assert list(rows) == list(settings.SETTINGS)
    env = {e["key"]: e.get("value") for e in yaml.safe_load((ROOT / "render.yaml").read_text())["services"][0]["envVars"]}
    code = (ROOT / "sphereblink" / "config.py").read_text()
    for key, s in settings.SETTINGS.items():
        value, lo, hi, note = rows[key]
        assert (lo, hi, note) == (s.lo, s.hi, s.note), key
        name = "SPHEREX_" + s.attr
        if name in env:
            production = float(env[name])  # what production runs with today
        else:  # not set there: the default written in config.py
            production = float(re.search(rf'os\.environ\.get\("{name}", "([\d.]+)"\)', code).group(1))
        assert value == production and lo <= value <= hi, key
    # with these values nothing is corrected: the table as created changes nothing on the server
    assert sum(rows[k][0] * (250 if k == "measure_procs" else 1) for k in settings.MEMORY) == 4900
    sql = (ROOT / "sql" / "settings.sql").read_text() + (ROOT / "sql" / "roles.sql").read_text()
    assert "enable row level security" in sql and "grant select on settings to blink_service" in sql
    assert not re.search(r"grant[^;]*(insert|update|delete|all)[^;]*on settings", sql)  # the service only reads it


@pytest.mark.skipif(not os.environ.get("SPHEREX_TEST_DATABASE_URL"), reason="needs a Postgres with sql/settings.sql loaded")
def test_against_a_real_database(monkeypatch):
    """SPHEREX_TEST_DATABASE_URL: a database with schema.sql, settings.sql and roles.sql, as blink_service.
    SPHEREX_TEST_DATABASE_OWNER_URL: the same database as its owner, to change a row."""
    import psycopg

    monkeypatch.setattr(config, "DB_URL", os.environ["SPHEREX_TEST_DATABASE_URL"])
    for name, value in PRODUCTION.items():
        monkeypatch.setattr(config, name, value)
    assert settings.refresh() and not settings._notes and settings.get("active_views") == 4
    assert all(v["from"] == "database" for v in settings.status()["settings"].values())
    with pytest.raises(psycopg.errors.InsufficientPrivilege), psycopg.connect(config.DB_URL) as conn:
        conn.execute("update settings set value = 5 where key = 'active_views'")
    with psycopg.connect(os.environ["SPHEREX_TEST_DATABASE_OWNER_URL"], autocommit=True) as owner:
        try:
            with pytest.raises(psycopg.errors.CheckViolation):
                owner.execute("update settings set value = 500 where key = 'active_views'")
            before = owner.execute("select updated_at from settings where key = 'active_views'").fetchone()[0]
            owner.execute("update settings set value = 6 where key = 'active_views'")
            assert owner.execute("select updated_at from settings where key = 'active_views'").fetchone()[0] > before
            assert settings.refresh() and settings.get("active_views") == 6
            owner.execute("delete from settings where key = 'active_views'")
            assert settings.refresh() and settings.get("active_views") == 4
            assert settings.status()["settings"]["active_views"]["from"] == "default"
        finally:
            owner.execute("insert into settings (key, value, min_value, max_value) values ('active_views', 4, 1, 8) "
                          "on conflict (key) do update set value = 4")
    monkeypatch.setattr(config, "DB_URL", os.environ["SPHEREX_TEST_DATABASE_URL"].replace("/blink", "/no_such_database"))
    assert not settings.refresh() and settings.get("active_views") == 4 and settings.status()["problem"]


def test_reading_the_table_asks_for_two_columns_and_gives_up_instead_of_hanging(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    seen = {}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            seen.setdefault("sql", []).append(sql)
            return self

        def fetchall(self):
            return [(b"active_views", 6.0), ("measure_procs", 1.0)]  # a database without an encoding gives bytes

    def connect(url, **options):
        seen.update(url=url, options=options)
        return Conn()

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(config, "DB_URL", "postgresql://example.invalid/db")
    assert settings.refresh() and settings.get("active_views") == 6 and settings.get("measure_procs") == 1
    assert seen["sql"] == ["set local statement_timeout = 5000", "select key, value from settings"]
    # the pooler of the database takes no prepared statements; a silent connection is given up
    assert seen["options"] == {"connect_timeout": 5, "prepare_threshold": None, "tcp_user_timeout": 10000}


def test_health_tells_held_memory_from_file_cache(tmp_path):
    """The host's graph counts the file cache as memory in use; /api/health
    separates what can be given back from what cannot."""
    from sphereblink import memory

    assert memory.container(tmp_path) is None and memory.free_mb(tmp_path) is None  # no limit to read
    (tmp_path / "memory.max").write_text("8000000000\n")
    (tmp_path / "memory.current").write_text("7500000000\n")
    (tmp_path / "memory.stat").write_text("anon 4000000000\nfile 3300000000\nshmem 300000000\ninactive_file 1000000000\nactive_file 2000000000\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 12\noom 1\noom_kill 1\n")
    c = memory.container(tmp_path)
    assert c["limit_mb"] == 8000.0 and c["file_cache_mb"] == 3000.0
    assert c["used_mb"] == 4500.0 and c["free_mb"] == 3500.0  # the cache can be given back
    assert c["working_set_mb"] == 6500.0  # what a platform's graph shows
    assert c["anon_mb"] == 4000.0 and c["shmem_mb"] == 300.0 and c["oom_kills"] == 1 and "peak_mb" not in c
    (tmp_path / "memory.max").write_text("max\n")
    assert memory.container(tmp_path) is None

    proc = tmp_path / "proc"
    for pid, ppid, anon, shm in ((10, 1, 900000, 100000), (11, 10, 300000, 500000), (12, 11, 50000, 0), (99, 1, 7000000, 0)):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "status").write_text(f"Name:\tpython\nPPid:\t{ppid}\nRssAnon:\t{anon} kB\nRssShmem:\t{shm} kB\n")
    (proc / "self").mkdir()
    (proc / "13").mkdir()  # a process that ended while the list was read
    got = memory.processes(10, proc)
    assert [(p["role"], p["own_mb"], p["shared_mb"]) for p in got] == [("web", 921.6, 102.4), ("worker", 307.2, 512.0), ("worker", 51.2, 0.0)]


def test_health_answers_with_or_without_a_memory_limit():
    from fastapi.testclient import TestClient

    from sphereblink import main, memory

    data = TestClient(main.app).get("/api/health").json()
    assert data["ok"] is True and "memory" in data
    assert data["commit"] is None or re.fullmatch(r"[0-9a-f]{7,40}", data["commit"])  # the source of what runs
    m = memory.usage()
    assert m is None or (m["free_mb"] <= m["limit_mb"] and m["processes"][0]["own_mb"] > 0)
