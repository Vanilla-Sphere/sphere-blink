"""The sky photograph kept on our own disk: everything offline, the archive is played by a function."""

import os
import threading
import time

import httpx
import pytest

from sphereblink import atlas, guard

JPEG = b"\xff\xd8\xff\xe0" + b"sky " * 600 + b"\xff\xd9"


class Archive:
    """Stands in for the archive: counts what it is asked and can be switched off."""

    def __init__(self):
        self.asked: list[str] = []
        self.up = True
        self.answer = lambda path: httpx.Response(200, content=JPEG + path.encode(), headers={"content-type": "image/jpeg"})
        self.slow = 0.0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        if self.slow:
            time.sleep(self.slow)
        if not self.up:
            raise httpx.ConnectError("the archive is away")
        return self.answer(request.url.path)


def make(tmp_path, archive, **kw):
    a = atlas.Atlas(root=tmp_path / "2mass", upstream="https://archive.test/hips/2MASS",
                    client=httpx.Client(transport=httpx.MockTransport(archive)), **kw)
    a.scan()
    return a


def test_a_tile_is_fetched_once_and_then_comes_from_disk(tmp_path):
    archive = Archive()
    a = make(tmp_path, archive)
    first, kept = a.get(3, 300)
    assert not kept and first.startswith(JPEG)
    assert archive.asked == ["/hips/2MASS/Norder3/Dir0/Npix300.jpg"]  # the layout of HiPS
    assert (tmp_path / "2mass/Norder3/Dir0/Npix300.jpg").read_bytes() == first  # stored byte for byte
    again, kept = a.get(3, 300)
    assert kept and again == first and len(archive.asked) == 1
    a.get(6, 30123)
    assert archive.asked[-1].endswith("/Norder6/Dir30000/Npix30123.jpg")

    archive.up = False  # the archive goes away: what is on disk is still there, the rest is refused
    assert a.get(3, 300) == (first, True)
    with pytest.raises(atlas.Away):
        a.get(3, 301)

    b = make(tmp_path, archive)  # a restart finds the tiles again
    assert b.get(3, 300) == (first, True) and b.stats()["tiles"] == 2


def test_only_real_tiles_are_asked_for_and_stored(tmp_path):
    archive = Archive()
    a = make(tmp_path, archive)
    for order, pix in ((-1, 0), (10, 0), (2, 192), (0, 12), (3, -1)):
        with pytest.raises(atlas.NotThere):
            a.get(order, pix)
    assert archive.asked == []  # an address outside the atlas never reaches the archive

    archive.answer = lambda path: httpx.Response(200, content=b"<html>maintenance</html>" * 100)
    with pytest.raises(atlas.Away):
        a.get(4, 7)
    assert not a.path(4, 7).exists()  # what is not a picture is not kept
    archive.answer = lambda path: httpx.Response(404)
    with pytest.raises(atlas.NotThere):
        a.get(4, 8)
    n = len(archive.asked)
    with pytest.raises(atlas.NotThere):
        a.get(4, 8)
    assert len(archive.asked) == n  # "no such tile" is believed for a while


def test_a_nearly_full_disk_gets_no_new_tiles(tmp_path, monkeypatch):
    archive = Archive()
    a = make(tmp_path, archive)
    monkeypatch.setattr(atlas.shutil, "disk_usage", lambda p: type("U", (), {"free": 0.5e9})())
    a._free = (0.0, 0.0)
    data, kept = a.get(4, 1)
    assert data.startswith(JPEG) and not kept and not a.path(4, 1).exists()  # shown, not kept
    assert a.stats()["disk_free_bytes"] == 0.5e9


def test_many_callers_of_one_tile_share_one_fetch(tmp_path):
    archive = Archive()
    archive.slow = 0.2
    a = make(tmp_path, archive)
    got = []
    threads = [threading.Thread(target=lambda: got.append(a.get(5, 4242)[0])) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 8 and len(set(got)) == 1 and len(archive.asked) == 1


def test_a_silent_archive_is_left_alone_for_a_while(tmp_path):
    archive = Archive()
    archive.up = False
    a = make(tmp_path, archive)
    for pix in range(atlas.REST_AFTER):
        with pytest.raises(atlas.Away):
            a.get(4, pix)
    assert len(archive.asked) == atlas.REST_AFTER and a.stats()["archive_resting"]
    with pytest.raises(atlas.Away):
        a.get(4, 99)
    assert len(archive.asked) == atlas.REST_AFTER  # refused at once, no request sent


def test_above_the_budget_the_deep_tiles_asked_for_longest_ago_go_first(tmp_path):
    archive = Archive()
    size = len(JPEG) + len("/hips/2MASS/Norder6/Dir0/Npix0.jpg")
    a = make(tmp_path, archive, budget_bytes=10.5 * size, keep_order=3)
    for pix in range(4):
        a.get(2, pix)  # coarse tiles: always kept
    for pix in range(6):
        a.get(6, pix)
        os.utime(a.path(6, pix), (1000 + pix, 1000 + pix))
    a = make(tmp_path, archive, budget_bytes=10.5 * size, keep_order=3)  # a restart reads the times from disk
    a.get(6, 0)  # asked for again: now the most recent of the deep ones
    a.get(6, 6)  # the eleventh tile: over the budget
    s = a.stats()
    assert s["bytes"] <= 0.9 * 10.5 * size and s["kept_tiles"] == 4 and s["dropped"] >= 1
    assert all(a.path(2, pix).exists() for pix in range(4))
    assert a.path(6, 0).exists() and a.path(6, 6).exists()
    assert not a.path(6, 1).exists()  # the one nobody asked for the longest


def test_the_kept_orders_are_filled_after_a_start_and_a_restart_carries_on(tmp_path):
    archive = Archive()
    a = make(tmp_path, archive, keep_order=1)
    a.get(1, 5)
    a.seed(threading.Event())
    s = a.stats()
    assert s["kept_tiles"] == s["kept_tiles_wanted"] == 12 + 48 and s["seeding"] is None
    assert len(archive.asked) == 60  # the one that was there is not fetched again
    b = make(tmp_path, archive, keep_order=1)
    b.seed(threading.Event())
    assert len(archive.asked) == 60


def test_the_server_answers_tiles_and_says_what_it_keeps(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sphereblink import main

    archive = Archive()
    monkeypatch.setattr(main, "sky", make(tmp_path, archive))
    c = TestClient(main.app)
    r = c.get("/api/atlas/2mass/Norder3/Dir0/Npix300.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content.startswith(JPEG)
    assert r.headers["x-atlas"] == "fetched" and "immutable" in r.headers["cache-control"]
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert "content-encoding" not in r.headers  # a JPEG is not compressed again
    r = c.get("/api/atlas/2mass/Norder3/Dir0/Npix300.jpg", headers={"Origin": "https://dev.example"})
    assert r.headers["x-atlas"] == "kept" and r.headers["access-control-allow-origin"] == "*"  # the page reads its pixels
    assert len(archive.asked) == 1
    for bad in ("Norder3/Dir10000/Npix300.jpg", "Norder10/Dir0/Npix1.jpg", "Norder3/Dir0/Npix768.jpg", "Norder3/Dir0/Npixx.jpg"):
        assert c.get(f"/api/atlas/2mass/{bad}").status_code in (404, 422)
    assert len(archive.asked) == 1
    archive.up = False
    r = c.get("/api/atlas/2mass/Norder3/Dir0/Npix301.jpg")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    s = c.get("/api/atlas").json()
    assert s["tiles"] == 1 and s["from_disk"] == 1 and s["fetched"] == 1 and "2MASS" in s["credit"]


def test_a_caller_may_only_make_the_server_fetch_so_many_new_tiles(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sphereblink import main

    archive = Archive()
    monkeypatch.setattr(main, "sky", make(tmp_path, archive))
    monkeypatch.setattr(guard, "atlas", guard.Buckets(1 / 60.0, 2))
    c = TestClient(main.app)
    who = {"CF-Connecting-IP": "203.0.113.77"}
    assert [c.get(f"/api/atlas/2mass/Norder4/Dir0/Npix{p}.jpg", headers=who).status_code for p in range(3)] == [200, 200, 429]
    assert c.get("/api/atlas/2mass/Norder4/Dir0/Npix0.jpg", headers=who).status_code == 200  # a kept tile costs nothing
    assert len(archive.asked) == 2

    # tiles are counted apart from the API calls: looking at the map does not use up what views need
    monkeypatch.setattr(guard, "requests", guard.Buckets(1.0, 3))
    monkeypatch.setattr(guard, "sky_tiles", guard.Buckets(1.0, 5))
    other = {"CF-Connecting-IP": "203.0.113.78"}
    codes = [c.get("/api/atlas/2mass/Norder4/Dir0/Npix0.jpg", headers=other).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2
    assert c.get("/api/featured", headers=other).status_code == 200


def test_the_page_asks_our_server_first_and_the_archive_second():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "web" / "atlas.js").read_text()
    assert 'const BASE = "/api/atlas/2mass"' in js
    archive = js.split('const ARCHIVE = "')[1].split('"')[0]
    assert archive.startswith("https://irsa.ipac.caltech.edu/") and "https://irsa.ipac.caltech.edu" in guard.CSP  # the policy lets the second source through
