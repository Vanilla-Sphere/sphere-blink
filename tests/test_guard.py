"""Limits, headers, fair sharing and second sources: everything offline."""

import asyncio
import threading
import time

import httpx
import numpy as np
import pytest

from sphereblink import config, cutout, guard, resolve
from sphereblink.cutout import Frame, Grid
from sphereblink.sia import Exposure


def exposure(k=0):
    return Exposure(obs_id=f"2025W30_1A_{k:04d}_1", detector=3, collection="spherex_qr3", mjd_start=60900.0 + k,
                    mjd_end=60900.001 + k, wave_min_um=1.6, wave_max_um=2.4, s3_key=f"qr3/level2/f{k}.fits",
                    access_url=f"https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/f{k}.fits", s_region="",
                    ra_center=10.0, dec_center=10.0)


def frame(k=0, n=21):
    return Frame(exposure=exposure(k), image=np.zeros((n, n), np.float32), wave_um=2.0, bandwidth_um=0.05, flux_ujy=1.0,
                 flux_err_ujy=0.1, flags_at_target=[], good_fraction=1.0)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from sphereblink import main

    return TestClient(main.app)


# ----------------------------------------------------------------------------- who is calling
def test_caller_is_the_address_cloudflare_saw_not_one_the_visitor_wrote():
    scope = {"headers": [(b"x-forwarded-for", b"6.6.6.6, 172.70.1.1"), (b"cf-connecting-ip", b"203.0.113.9")], "client": ("10.0.0.5", 1)}
    assert guard.client_of(scope) == "203.0.113.9"
    assert guard.client_of({"headers": [(b"x-forwarded-for", b"198.51.100.7, 10.1.2.3")], "client": ("10.0.0.5", 1)}) == "198.51.100.7"
    assert guard.client_of({"headers": [], "client": ("10.0.0.5", 1)}) == "10.0.0.5"
    # one visitor has a whole IPv6 /64: every address in it is the same caller
    a = guard.client_of({"headers": [(b"cf-connecting-ip", b"2001:db8:1:2:aaaa::1")]})
    b = guard.client_of({"headers": [(b"cf-connecting-ip", b"2001:db8:1:2:bbbb::9")]})
    assert a == b == "2001:db8:1:2::/64"
    assert guard.client_of({"headers": [(b"cf-connecting-ip", b"not an address")]}) == "unknown"
    assert guard.internal("10.0.0.5") and guard.internal("127.0.0.1") and not guard.internal("203.0.113.9")


def test_bucket_allows_a_burst_then_the_rate_and_never_limits_the_inside():
    b = guard.Buckets(per_second=10.0, burst=3)
    assert [b.take("203.0.113.9") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = b.take("203.0.113.9")
    assert 0.05 < wait <= 0.1  # one token at 10 per second
    assert b.take("198.51.100.7") == 0.0  # another caller has a bucket of its own
    assert all(b.take("10.0.0.5") == 0.0 for _ in range(50))  # health checks, the server itself
    time.sleep(0.12)
    assert b.take("203.0.113.9") == 0.0


# ----------------------------------------------------------------------------- headers
def test_every_answer_carries_the_security_headers(client):
    page = client.get("/")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'self'" in csp and "unsafe-eval" not in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]  # no inline script
    assert page.headers["x-frame-options"] == "SAMEORIGIN"
    for r in (page, client.get("/api/health"), client.get("/static/app.js"), client.get("/nothing-here")):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["strict-transport-security"].startswith("max-age=")
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    data = client.get("/api/health")
    assert data.headers["content-security-policy"] == "default-src 'none'"
    assert data.headers["cross-origin-resource-policy"] == "cross-origin"  # pictures and data may be shown elsewhere
    docs = client.get("/docs")
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]  # the API pages load Swagger UI


def test_no_other_site_can_frame_a_page_and_pictures_stay_free(client):
    """Clickjacking: every answer that is a page says "only this site may
    frame me", twice (CSP frame-ancestors, X-Frame-Options). Pictures and data
    say nothing of the kind: they are meant to be shown elsewhere."""
    for path in ("/", "/static/index.html", "/docs", "/redoc"):
        page = client.get(path)
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html"), path
        policy = [d.strip() for d in page.headers["content-security-policy"].split(";")]
        assert "frame-ancestors 'self'" in policy, path
        assert page.headers["x-frame-options"] == "SAMEORIGIN", path
    assert guard.FRAMING in guard.security_headers("/", b"application/xhtml+xml")
    for path in ("/api/health", "/static/app.js", "/static/favicon.svg", "/apple-touch-icon.png", "/nothing-here"):
        other = client.get(path, headers={"Origin": "https://elsewhere.example"})
        assert "x-frame-options" not in other.headers, path
        assert "frame-ancestors" not in other.headers["content-security-policy"], path
        assert other.headers["cross-origin-resource-policy"] == "cross-origin", path  # <img> on another site
    assert client.get("/api/health", headers={"Origin": "https://elsewhere.example"}).headers["access-control-allow-origin"] == "*"  # fetch from another site


def test_not_modified_does_not_replace_the_policy_of_the_page(client):
    """A browser lays the headers of a 304 over the ones it kept. The 304 has
    no type, so it must not arrive with the policy of data: the page would
    lose its own policy, frame-ancestors with it."""
    first = client.get("/static/index.html")
    again = client.get("/static/index.html", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    for name in ("content-security-policy", "x-frame-options", "cross-origin-resource-policy"):
        assert name not in again.headers, name
    assert again.headers["x-content-type-options"] == "nosniff"


def test_the_dev_site_is_the_viewer_in_front_of_the_live_engine(tmp_path):
    """render.dev.yaml: the page of the branch develop as a static site, with
    /api/ passed on to production (docs/environments.md)."""
    yaml = pytest.importorskip("yaml")
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    (prod,), (dev,) = (yaml.safe_load((root / f).read_text())["services"] for f in ("render.yaml", "render.dev.yaml"))
    live = prod["domains"][0]
    assert dev["runtime"] == "static" and dev["branch"] == "develop" and dev["name"] == prod["name"] + "-dev"
    assert not set(dev.get("domains", [])) & set(prod["domains"])  # never a name of the live site
    assert "envVars" not in dev and "disk" not in dev  # nothing of its own behind the page, and no secret
    routes = {r["source"]: r for r in dev["routes"]}
    assert routes["/api/*"] == {"type": "rewrite", "source": "/api/*", "destination": f"https://{live}/api/*"}
    headers = {(h["path"], h["name"].lower()): h["value"] for h in dev["headers"]}
    assert headers["/*", "x-robots-tag"] == "noindex, nofollow"
    assert headers["/", "content-security-policy"] == guard.CSP  # the page is held to production's policy
    assert headers["/index.html", "content-security-policy"] == guard.CSP  # ... under its other address too
    assert headers["/*", "x-frame-options"].encode() == guard.FRAMING[1]  # no path of the copy can be framed elsewhere
    assert len(headers) == len(dev["headers"])  # no two rules for one header on one path: which would win is not ours to say
    assert "render.dev.yaml" in prod["buildFilter"]["ignoredPaths"]  # a change to dev does not restart production

    # the site is laid out the way the server of production serves web/
    out = tmp_path / "site"
    subprocess.run(["sh", str(root / "scripts" / "build_dev_site.sh"), str(out)], check=True, capture_output=True)
    assert dev["buildCommand"].split()[:2] == ["sh", "scripts/build_dev_site.sh"]
    page = (out / "index.html").read_text()
    assert "<title>[dev] " in page and page.replace("<title>[dev] ", "<title>") == (root / "web" / "index.html").read_text()
    for name in ("app.js", "style.css", "data/sky.json", "fonts/HankenGrotesk.woff2"):
        assert (out / "static" / name).read_bytes() == (root / "web" / name).read_bytes()
    assert (out / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"


def test_other_sites_may_read_but_only_read(client):
    r = client.get("/api/health", headers={"Origin": "https://example.org"})
    assert r.headers["access-control-allow-origin"] == "*" and "access-control-allow-credentials" not in r.headers
    pre = client.options("/api/view", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 400
    assert client.post("/api/view?ra=1&dec=1").status_code == 405


# ----------------------------------------------------------------------------- input
def test_numbers_that_are_not_a_place_never_reach_the_name_services(client, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a number was sent to the name services")

    monkeypatch.setattr(resolve, "resolve_name", no_network)
    for q in ("ra=nan&dec=nan", "ra=inf&dec=0", "ra=10&dec=-inf", "ra=500&dec=3", "ra=10&dec=-91", "ra=-0.5&dec=0"):
        r = client.get(f"/api/view?{q}")
        assert r.status_code == 400 and "out of range" in r.json()["detail"], q
    for q in ("%3Cscript%3Ealert(1)%3C/script%3E", "%22;drop%20table", "%20%20", "....", "a" * 200):
        assert client.get(f"/api/resolve?q={q}").status_code in (400, 422), q
    # a very small right ascension is a place: it used to be written as "1e-05" and looked up as a name
    t = resolve.at(0.00001, 2.0)
    assert (t.ra, t.dec, t.source) == (0.00001, 2.0, "coordinates")
    with pytest.raises(resolve.ResolveError):
        resolve.at(float("nan"), 1.0)
    assert resolve.clean_name("  <b>Bar\x00nard</b>\n" + "x" * 300)[:12] == "<b>Barnard</" and len(resolve.clean_name("x" * 300)) == 80


def test_a_label_cannot_store_a_measurement_under_a_featured_name(monkeypatch):
    from sphereblink import measure_api

    started = {}
    monkeypatch.setattr(measure_api, "_svc", object())
    monkeypatch.setattr(measure_api, "start", lambda t, refresh, charge=False: (started.setdefault("key", measure_api.job_key(t)), {"status": "queued"}))
    out = measure_api.api_measure(target="Barnard's Star", ra=10.0, dec=10.0, refresh=True, detections=False)
    assert started["key"].startswith("pos_") and out["target"]["name"].startswith("RA ")
    with pytest.raises(Exception) as bad:
        measure_api.api_measure(target=None, ra=float("nan"), dec=1.0, refresh=False, detections=False)
    assert getattr(bad.value, "status_code", 0) == 400


def test_measurements_from_outside_are_counted_and_the_queue_is_bounded(monkeypatch):
    from sphereblink import measure_api, measurements

    monkeypatch.setattr(measurements, "load_local", lambda key: None)
    monkeypatch.setattr(measure_api, "_work", lambda key, target: None)
    monkeypatch.setattr(measure_api, "_jobs", {})
    monkeypatch.setattr(guard, "measures", guard.Buckets(per_second=0.001, burst=2))
    token = guard._client.set("203.0.113.9")
    try:
        for k in range(2):
            measure_api.start(resolve.at(10.0 + k, 5.0), charge=True)
        with pytest.raises(guard.Busy) as busy:
            measure_api.start(resolve.at(20.0, 5.0), charge=True)
        assert busy.value.status == 429
        assert measure_api.start(resolve.at(10.0, 5.0), charge=True)[1]["status"] == "queued"  # already running: no charge
        measure_api.start(resolve.at(30.0, 5.0))  # the server's own measurements are never refused
        monkeypatch.setattr(config, "MAX_MEASURE_QUEUE", 3)
        monkeypatch.setattr(guard, "measures", guard.Buckets(per_second=1.0, burst=50))
        with pytest.raises(guard.Busy) as full:
            measure_api.start(resolve.at(40.0, 5.0), charge=True)
        assert full.value.status == 503
    finally:
        guard._client.reset(token)


# ----------------------------------------------------------------------------- limits
def test_a_flood_of_requests_is_answered_with_429_and_retry_after(client, monkeypatch):
    monkeypatch.setattr(guard, "requests", guard.Buckets(per_second=0.5, burst=3))
    codes = [client.get("/api/featured").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = client.get("/api/featured")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "detail" in r.json()
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/health").status_code == 200  # the host's health check is never limited
    assert client.get("/").status_code == 200  # nor the page itself


def test_one_caller_cannot_fill_the_server_with_views(tmp_path, monkeypatch):
    from sphereblink.service import Service, Store

    svc = Service(Store(tmp_path / "c.sqlite"))
    monkeypatch.setattr(svc, "_run", lambda job, grid: None)  # jobs stay "queued"
    monkeypatch.setattr(config, "MAX_JOBS_PER_CLIENT", 3)
    monkeypatch.setattr(config, "MAX_JOBS", 5)
    grids = [Grid.from_fov(10.0 + k, 5.0, 8.0) for k in range(8)]
    jobs = [svc.start(g, client="203.0.113.9") for g in grids[:3]]
    with pytest.raises(guard.Busy) as busy:
        svc.start(grids[3], client="203.0.113.9")
    assert busy.value.status == 429
    assert svc.start(grids[0], client="203.0.113.9") is jobs[0]  # a view that exists is always given
    svc.start(grids[3], client="198.51.100.7")
    svc.start(grids[4], client="198.51.100.7")
    with pytest.raises(guard.Busy) as full:
        svc.start(grids[5], client="192.0.2.44")
    assert full.value.status == 503
    svc.start(grids[5], client="")  # the server's own work
    jobs[0].status = "ready"  # a finished view frees its place
    monkeypatch.setattr(config, "MAX_JOBS", 50)
    svc.start(grids[6], client="203.0.113.9")


# ----------------------------------------------------------------------------- the pipeline queue
def _views(s, *rows, mb=0.0):
    for name, client, started, pending, sent, active in rows:
        s._views[name] = {"name": name, "client": client, "started": started, "pending": list(range(pending)), "sent": sent,
                          "active": active, "mb": mb}


def test_scheduler_works_on_two_views_and_the_others_wait_their_turn(monkeypatch):
    from sphereblink.service import Scheduler

    monkeypatch.setattr(config, "ACTIVE_VIEWS", 2)
    monkeypatch.setattr(config, "QUEUE_PREVIEW", False)
    s = Scheduler(lambda: None, 4)
    _views(s, ("v1", "a", 1.0, 20, 0, False), ("v2", "b", 2.0, 20, 0, False), ("v3", "c", 3.0, 20, 0, False),
           ("v4", "d", 4.0, 20, 0, False))
    s._admit()
    assert [n for n, v in s._views.items() if v["active"]] == ["v1", "v2"]
    assert s.place("v1") == (0, 0) and s.place("v3") == (1, 2) and s.place("v4") == (2, 2)
    for _ in range(12):  # whatever is picked, it is one of the two: the limit is hard
        v = s._next()
        assert v["name"] in ("v1", "v2")
        v["pending"].pop()
        v["sent"] += 1
    del s._views["v1"]  # a view is done: the first of the queue has its turn
    s._admit()
    assert s._views["v3"]["active"] and s.place("v4") == (1, 1) and s.place("gone") == (0, 0)
    monkeypatch.setattr(config, "ACTIVE_VIEWS", 0)  # no limit
    s._admit()
    assert all(v["active"] for v in s._views.values())


def test_a_waiting_view_gets_its_first_exposures_and_no_more(monkeypatch):
    from sphereblink.service import Scheduler

    monkeypatch.setattr(config, "ACTIVE_VIEWS", 2)
    monkeypatch.setattr(config, "QUEUE_PREVIEW", True)
    s = Scheduler(lambda: None, 4)
    _views(s, ("mosaic", "a", 1.0, 60, 30, True), ("small", "b", 2.0, 10, 5, True), ("waits", "c", 3.0, 20, 0, False))
    s._picks = 0
    assert s._next()["name"] == "waits"  # not served yet: a first picture, even from the queue
    s._views["waits"]["sent"] = 1
    assert s._next()["name"] == "small"  # then only the two being worked on: the least left to do
    assert s._next()["name"] == "small"
    assert s._next()["name"] == "mosaic"  # every fourth chunk: the one that started first
    assert all(s._next()["name"] != "waits" for _ in range(8))
    assert s.place("waits") == (1, 1)


def test_callers_take_turns_in_the_queue(monkeypatch):
    from sphereblink.service import Scheduler

    monkeypatch.setattr(config, "ACTIVE_VIEWS", 2)
    s = Scheduler(lambda: None, 4)
    _views(s, ("z1", "z", 1.0, 5, 1, True), ("z2", "z", 1.1, 5, 1, True), ("z3", "z", 1.2, 5, 0, False),
           ("z4", "z", 1.3, 5, 0, False), ("late", "y", 9.0, 5, 0, False))
    # one caller opened four views; somebody else's first view goes before that caller's third
    assert [v["name"] for v in s._queue()] == ["late", "z3", "z4"]
    assert s.place("late") == (1, 3) and s.place("z3") == (2, 3)
    del s._views["z1"]
    s._admit()
    assert s._views["late"]["active"] and not s._views["z3"]["active"]


def test_wide_mosaics_wait_for_memory_and_small_views_pass_them(monkeypatch):
    from sphereblink.service import Scheduler

    monkeypatch.setattr(config, "ACTIVE_VIEWS", 4)
    monkeypatch.setattr(config, "ACTIVE_MB", 2400.0)
    s = Scheduler(lambda: None, 4)
    _views(s, ("wide1", "a", 1.0, 40, 0, False), ("wide2", "b", 2.0, 40, 0, False), ("wide3", "c", 3.0, 40, 0, False), mb=1100.0)
    _views(s, ("small1", "d", 4.0, 20, 0, False), ("small2", "e", 5.0, 20, 0, False), ("small3", "f", 6.0, 20, 0, False), mb=9.0)
    s._admit()
    active = [n for n, v in s._views.items() if v["active"]]
    assert active == ["wide1", "wide2", "small1", "small2"]  # two mosaics fill the memory; four views, one per process
    assert s.place("wide3") == (1, 2) and s.place("small3") == (2, 2)
    del s._views["small1"]
    s._admit()
    assert s._views["small3"]["active"] and not s._views["wide3"]["active"]  # the third mosaic still does not fit
    del s._views["wide1"]
    s._admit()
    assert s._views["wide3"]["active"]
    # one view is always worked on, however heavy
    s2 = Scheduler(lambda: None, 4)
    _views(s2, ("huge", "a", 1.0, 40, 0, False), mb=5000.0)
    s2._admit()
    assert s2._views["huge"]["active"]


def test_a_view_in_the_queue_says_so(monkeypatch):
    from sphereblink.service import Job

    job = Job(id="j", status="fetching", total=300, done=4)
    job.place = lambda: (3, 8)
    out = job.public()
    assert out["status"] == "queued" and out["queue"] == {"position": 3, "waiting": 8} and out["done"] == 4
    job.place = lambda: (0, 0)
    out = job.public()
    assert out["status"] == "fetching" and out["queue"] is None


def test_two_measurements_at_a_time_and_the_third_knows_its_place(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sphereblink import measure_api, measurements

    release, running = threading.Event(), []

    def fake_run(target, exposures, wave_maps, concurrency):
        running.append((target.name, concurrency))
        release.wait(10)
        return {"stats": {}, "track": None}

    class Svc:
        def exposures(self, ra, dec):
            return [exposure(0)]

        def wave_map(self, e):
            return None

    pool = ThreadPoolExecutor(2)
    monkeypatch.setattr(config, "MEASURE_PROCS", 2)
    monkeypatch.setattr(measure_api, "_svc", Svc())
    monkeypatch.setattr(measure_api, "_executor", lambda: pool)
    monkeypatch.setattr(measure_api, "run", fake_run)
    monkeypatch.setattr(measure_api, "_jobs", {})
    monkeypatch.setattr(measure_api, "_waiting", [])
    monkeypatch.setattr(measure_api, "_measuring", 0)
    monkeypatch.setattr(measurements, "load_local", lambda key: None)
    monkeypatch.setattr(measurements, "save_local", lambda key, result: None)
    monkeypatch.setattr(measurements, "save_db", lambda result: False)
    try:
        jobs = []
        for k in range(4):
            jobs.append(measure_api.start(resolve.at(10.0 + k, 5.0)))
            for _ in range(200):  # in the order they came: wait until this one runs or waits
                if len(running) > k or measure_api.place(jobs[-1][0])[0]:
                    break
                time.sleep(0.01)
        assert [j["status"] for _, j in jobs] == ["measuring", "measuring", "queued", "queued"]
        assert len(running) == 2 and running[0][1] == 8  # 16 connections to the bucket, shared by the two
        assert measure_api._public(jobs[2][1])["queue"] == {"position": 1, "waiting": 2}
        assert measure_api._public(jobs[3][1])["queue"] == {"position": 2, "waiting": 2}
        assert measure_api._public(jobs[0][1])["queue"] is None
        release.set()
        for _ in range(500):
            if all(j["status"] == "ready" for _, j in jobs):
                break
            time.sleep(0.01)
        assert [j["status"] for _, j in jobs] == ["ready"] * 4 and len(running) == 4
        assert measure_api._measuring == 0 and measure_api._waiting == []
    finally:
        release.set()
        pool.shutdown()


def test_scheduler_hands_every_chunk_back_and_survives_a_broken_pool():
    from concurrent.futures import ThreadPoolExecutor

    from sphereblink import service, worker

    pool = ThreadPoolExecutor(2)
    calls = []

    def fake_cut(grid, items):
        calls.append(len(items))
        if items and items[0] == "boom":
            raise RuntimeError("worker died")
        return [(i, None, None, "") for i in items], {"requests": len(items)}

    real, worker.cut_chunk = worker.cut_chunk, fake_cut
    try:
        s = service.Scheduler(lambda: pool, 3)
        got = list(s.run("j1", "a", None, [[1, 2], [3], [4, 5, 6]]))
        assert sorted(len(r) for r, _ in got) == [1, 2, 3] and not s._views
        with pytest.raises(RuntimeError):
            list(s.run("j2", "a", None, [["boom"], [1]]))
        assert not s._views and len(list(s.run("j3", "a", None, [[1]]))) == 1  # the next view is served as usual
    finally:
        worker.cut_chunk = real
        pool.shutdown()


# ----------------------------------------------------------------------------- caches
def test_disk_cache_drops_the_views_unused_longest_and_never_the_pinned(tmp_path):
    from sphereblink.service import Store

    store = Store(tmp_path / "c.sqlite")
    grids = [Grid.from_fov(10.0 + k, 5.0, 8.0) for k in range(4)]
    for g in grids:
        store.put_frames(g, [(f"{g.key()}_{i}", exposure(i), frame(i)) for i in range(3)])
    one = store._conn().execute("SELECT bytes FROM views LIMIT 1").fetchone()[0]
    assert one > 0 and store._conn().execute("SELECT COUNT(*) FROM views").fetchone()[0] == 4
    with store._conn() as c:  # fetched in the order 0..3; view 0 was opened again last
        for k, g in enumerate(grids):
            c.execute("UPDATE views SET used=? WHERE grid=?", (100.0 + k, g.key()))
    store._touched.clear()
    store.touch(grids[0].key())
    store.trim(2.5 * one, keep={grids[3].key()})
    left = {r[0] for r in store._conn().execute("SELECT DISTINCT grid FROM frames")}
    assert left == {grids[0].key(), grids[3].key()}  # 1 and 2 were unused longest
    store.trim(0, keep={grids[3].key()})
    assert {r[0] for r in store._conn().execute("SELECT grid FROM views")} == {grids[3].key()}


def test_a_cache_from_before_the_views_table_is_counted_once(tmp_path):
    from sphereblink.service import Store

    store = Store(tmp_path / "c.sqlite")
    g = Grid.from_fov(10.0, 5.0, 8.0)
    store.put_frames(g, [(f"k{i}", exposure(i), frame(i)) for i in range(2)])
    with store._conn() as c:
        c.execute("DELETE FROM views")
    store.count_views()
    assert store._conn().execute("SELECT grid, bytes > 0 FROM views").fetchall() == [(g.key(), 1)]


def test_resolved_names_are_kept_on_disk_and_serve_when_the_services_are_away(tmp_path, monkeypatch):
    from sphereblink import main
    from sphereblink.service import Store

    monkeypatch.setattr(main.svc, "store", Store(tmp_path / "c.sqlite"))
    monkeypatch.setattr(main, "_names", type(main._names)())
    monkeypatch.setattr(main, "_unknown_names", {})
    asked = []

    def lookup(text, client=None):
        asked.append(text)
        return resolve.Target(name="Vega (* alf Lyr)", ra=279.23, dec=38.78, ra_input=279.23, dec_input=38.78, source="SIMBAD")

    monkeypatch.setattr(main, "resolve", lookup)
    assert main._resolve_cached("Vega").source == "SIMBAD" and main._resolve_cached("vega").ra == 279.23
    assert asked == ["Vega"]
    monkeypatch.setattr(main, "_names", type(main._names)())  # a restart: memory is empty, the disk is not

    def down(text, client=None):
        raise resolve.ResolverUnavailable("the name services are not answering")

    monkeypatch.setattr(main, "resolve", down)
    assert main._resolve_cached("VEGA").name == "Vega (* alf Lyr)"
    monkeypatch.setattr(config, "NAME_TTL_DAYS", 0.0)  # too old to trust while the services answer ...
    monkeypatch.setattr(main, "_names", type(main._names)())
    assert main._resolve_cached("Vega").ra == 279.23  # ... but better than nothing when they do not
    with pytest.raises(resolve.ResolverUnavailable):
        main._resolve_cached("Altair")


def test_name_lookups_are_counted_per_caller_only_when_they_go_out(tmp_path, monkeypatch):
    from sphereblink import main
    from sphereblink.service import Store

    monkeypatch.setattr(main.svc, "store", Store(tmp_path / "c.sqlite"))
    monkeypatch.setattr(main, "_names", type(main._names)())
    monkeypatch.setattr(main, "_unknown_names", {})
    monkeypatch.setattr(guard, "names", guard.Buckets(per_second=0.001, burst=2))
    monkeypatch.setattr(main, "resolve", lambda text, client=None: resolve.Target(
        name=text, ra=1.0, dec=1.0, ra_input=1.0, dec_input=1.0, source="SIMBAD"))
    token = guard._client.set("203.0.113.9")
    try:
        for _ in range(20):  # coordinates, featured targets and names already known cost nothing
            main._resolve_cached("269.45 4.69")
        main._resolve_cached("Star One")
        main._resolve_cached("Star Two")
        for _ in range(20):
            main._resolve_cached("star one")
        with pytest.raises(guard.Busy):
            main._resolve_cached("Star Three")
    finally:
        guard._client.reset(token)


# ----------------------------------------------------------------------------- second source for the files
def test_files_come_from_the_archive_when_the_bucket_fails(monkeypatch):
    bucket = config.S3_HTTP_BASE + "/qr3/level2/f1.fits"
    assert cutout.mirror_url(bucket) == config.MIRROR_HTTP_BASE + "/qr3/level2/f1.fits"
    assert cutout.mirror_url("https://elsewhere.example/f1.fits") is None
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host.endswith("amazonaws.com"):
            if "missing" in request.url.path:
                return httpx.Response(404)
            raise httpx.ConnectError("bucket unreachable")
        if "missing" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(206, content=b"x" * 10, headers={"Content-Range": "bytes 0-9/100"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            monkeypatch.setattr(cutout, "MIRROR", {"failures": 0, "until": 0.0, "used": 0})
            for _ in range(cutout.MIRROR_AFTER):
                assert await cutout._get(c, bucket, 0, 10) == b"x" * 10
            assert seen.count("irsa.ipac.caltech.edu") == cutout.MIRROR_AFTER
            n = len(seen)
            assert await cutout._get(c, bucket, 0, 10) == b"x" * 10
            assert seen[n:] == ["irsa.ipac.caltech.edu"]  # the bucket is left alone for a while
            monkeypatch.setattr(cutout, "MIRROR", {"failures": 0, "until": 0.0, "used": 0})
            with pytest.raises(httpx.HTTPStatusError):  # missing in both places: an error, and no penalty for the bucket
                await cutout._get(c, config.S3_HTTP_BASE + "/qr3/missing.fits", 0, 10)
            assert cutout.MIRROR["failures"] == 0

    asyncio.run(run())


def test_an_older_exposure_list_is_given_at_once_and_refreshed_behind_the_scenes(tmp_path, monkeypatch):
    from sphereblink import retry, service

    svc = service.Service(service.Store(tmp_path / "c.sqlite"))
    asked, release, answer = [], threading.Event(), {}

    def search(points, timeout=0.0, **k):
        asked.append(timeout)
        if len(asked) == 1:
            return [exposure(1), exposure(2)]
        release.wait(5)  # the archive is slow ...
        if "error" in answer:
            raise answer["error"]  # ... or away
        return answer["found"]

    def settled(key):  # the refresh in the background has ended
        for _ in range(200):
            if svc._refreshing.get(key) != float("inf"):
                return
            time.sleep(0.01)
        raise AssertionError("still refreshing")

    monkeypatch.setattr(service, "find_exposures_at", search)
    assert len(svc.exposures(10.0, 5.0)) == 2
    (key,) = [k for (k,) in svc.store._conn().execute("SELECT key FROM exposure_lists")]

    def age():
        with svc.store._conn() as c:  # a day passes: the list is due for a refresh
            c.execute("UPDATE exposure_lists SET created = created - 2 * 86400")

    # the archive is away: the older list is the answer, and nobody waits for the search
    age()
    answer["error"] = retry.Unavailable("the archive", "HTTP 502", 9)
    t0 = time.time()
    assert len(svc.exposures(10.0, 5.0)) == 2
    assert time.time() - t0 < 1.0 and asked == [service.SEARCH_TIMEOUT_S, service.REFRESH_TIMEOUT_S]
    release.set()
    settled(key)
    assert len(svc.exposures(10.0, 5.0)) == 2 and len(asked) == 2  # a failed refresh is not repeated at every request

    # the archive is back and has a new exposure: the list on disk is brought up to date for the next request
    answer.pop("error")
    answer["found"] = [exposure(1), exposure(2), exposure(3)]
    svc._refreshing.clear()  # as if the pause after the failure were over
    assert len(svc.exposures(10.0, 5.0)) == 2
    settled(key)
    assert len(svc.exposures(10.0, 5.0)) == 3 and len(asked) == 3

    # a place never seen has nothing to fall back on: the failure is said in a sentence, without the address asked
    monkeypatch.setattr(service, "find_exposures_at", lambda points, timeout=0.0, **k: (_ for _ in ()).throw(answer["boom"]))
    answer["boom"] = retry.Unavailable("NASA's SPHEREx archive (IRSA)", "HTTP 502", 9)
    with pytest.raises(retry.Unavailable):
        svc.exposures(99.0, 5.0)
    job = svc.start(Grid.from_fov(99.0, 5.0, 8.0), client="")
    for _ in range(300):
        if job.status == "error":
            break
        time.sleep(0.01)
    assert job.status == "error"
    assert job.error == "NASA's SPHEREx archive (IRSA) is not answering right now (HTTP 502, asked 9 times). This usually passes within minutes."
    request = httpx.Request("GET", "https://irsa.ipac.caltech.edu/SIA?POS=circle+1+2+0.0005")
    raw = httpx.HTTPStatusError("Server error '502 Proxy Error' for url 'https://irsa...'\nFor more information check: https://developer.mozilla.org/",
                                request=request, response=httpx.Response(502, request=request))
    assert "http" not in service.say(raw) and "HTTP 502" in service.say(raw)
    assert len(service.say(ValueError("x" * 500))) < 180


def test_a_passing_failure_of_an_archive_is_asked_again_until_the_deadline():
    from sphereblink import retry

    def client(answers):
        seen = []

        def handler(request):
            seen.append(request)
            a = answers[min(len(seen) - 1, len(answers) - 1)]
            if isinstance(a, Exception):
                raise a
            return httpx.Response(a, text="ok" if a == 200 else "<h1>Proxy Error</h1>", headers={"retry-after": "2"} if a == 429 else {})

        return httpx.Client(transport=httpx.MockTransport(handler)), seen

    class Time:  # a clock that only moves when the code sleeps
        now = 0.0
        naps: list = []

        def sleep(self, s):
            self.naps.append(s)
            self.now += s

    def ask(answers, budget=60.0):
        c, seen = client(answers)
        t = Time()
        t.naps = []
        try:
            r = retry.get("https://archive.test/SIA", {"POS": "circle 1 2 0.0005"}, service="The archive", client=c,
                          budget=budget, sleep=t.sleep, clock=lambda: t.now)
            return r, seen, t
        except retry.Unavailable as exc:
            return exc, seen, t

    # five gateway errors in a row (what IRSA did on 2026-11-15), then the answer
    r, seen, t = ask([502, 502, 502, 502, 502, 200])
    assert r.status_code == 200 and len(seen) == 6
    assert all(0.25 <= a <= 4.0 for a in t.naps) and t.naps[3] >= 2.0  # the pauses grow, are capped, and are uneven
    # a timeout and a dropped connection are passing failures too
    r, seen, _ = ask([httpx.ReadTimeout("slow"), httpx.ConnectError("refused"), 200])
    assert r.status_code == 200 and len(seen) == 3
    # an archive that asks for a pause is given it
    r, seen, t = ask([429, 200])
    assert r.status_code == 200 and t.naps == [2.0]
    # away for good: asking stops at the deadline, and the error is a sentence without the address
    exc, seen, t = ask([502], budget=20.0)
    assert isinstance(exc, retry.Unavailable) and exc.reason == "HTTP 502" and exc.tries == len(seen) > 5
    assert t.now <= 20.0 and "archive.test" not in str(exc) and str(exc).startswith("The archive is not answering right now (HTTP 502, asked ")
    # a refusal that asking again cannot change is not repeated
    exc, seen, _ = ask([400, 200])
    assert isinstance(exc, retry.Unavailable) and exc.reason == "HTTP 400" and len(seen) == 1


def test_the_exposure_search_shares_one_deadline_and_names_the_archive(monkeypatch):
    from sphereblink import retry, sia

    seen = []

    def handler(request):
        seen.append(request.url.params["COLLECTION"])
        if len(seen) % 3:  # two in three fail, as on 2026-11-15
            return httpx.Response(502, text="<h1>Proxy Error</h1>")
        return httpx.Response(200, text="s_ra,s_dec\n", headers={"content-type": "text/csv"})

    monkeypatch.setattr(retry, "FIRST_PAUSE_S", 0.0)  # no waiting in a test
    c = httpx.Client(transport=httpx.MockTransport(handler))
    assert sia.find_exposures_at([(1.0, 2.0), (1.1, 2.0)], ["spherex_dr1", "spherex_qr3"], client=c, timeout=30.0) == []
    assert len(seen) >= 4 and set(seen) == {"spherex_dr1", "spherex_qr3"}
    down = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(502)))
    with pytest.raises(retry.Unavailable) as err:
        sia.find_exposures_at([(1.0, 2.0)], ["spherex_dr1"], client=down, timeout=30.0, budget=0.0)
    assert str(err.value).startswith("NASA's SPHEREx archive (IRSA) is not answering right now (HTTP 502")
