"""The blind search for moving objects, its store and its endpoints: everything offline."""

import math

import numpy as np
import pytest

from sphereblink import config, known_objects, mover_store, movers
from sphereblink.cutout import Grid

SCALE = 6.15


def times(passes=((61000.0, 9),), burst=4):
    """Exposure times as SPHEREx takes them: a few exposures two minutes apart, every orbit."""
    out = []
    for start, orbits in passes:
        for o in range(orbits):
            out += [start + o * 96.0 / 1440.0 + k * 2.2 / 1440.0 for k in range(burst)]
    return out


def field(mover=None, n=121, seed=1, stars=25, passes=((61000.0, 9),)):
    """A synthetic field: noise, stars that stay, and optionally one object
    that moves ``mover = (x0, y0, vx, vy, peak)`` in pixels and pixels per hour.
    Every time is seen by two detectors at once, as behind the dichroic."""
    rng = np.random.default_rng(seed)
    grid = Grid(ra=150.0, dec=10.0, npix=n, scale_arcsec=SCALE)
    yy, xx = np.mgrid[0:n, 0:n]
    sky = np.zeros((n, n))
    for _ in range(stars):
        x, y, a = rng.uniform(5, n - 5), rng.uniform(5, n - 5), rng.uniform(8, 300)
        sky += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8**2))
    ts = times(passes)
    frames, images = [], []
    for i, t in enumerate(ts):
        for det, colour in ((2, 1.0), (5, 0.6)):  # a star is not equally bright in both bands
            img = sky * colour + rng.normal(0, 1, (n, n))
            if mover is not None:
                x0, y0, vx, vy, peak = mover
                h = (t - ts[0]) * 24.0
                img += peak * np.exp(-((xx - x0 - vx * h) ** 2 + (yy - y0 - vy * h) ** 2) / (2 * 0.8**2))
            images.append(img.astype(np.float32))
            frames.append({"exposure": f"E{i:03d}:D{det}", "mjd": t, "detector": det, "wave_um": 1.2 if det == 2 else 4.0})
    return np.stack(images), frames, grid


def test_tangent_plane_goes_there_and_back():
    ra, dec = np.array([150.2, 149.7, 150.0]), np.array([10.3, 9.6, 10.0])
    e, n = movers.project(ra, dec, 150.0, 10.0)
    assert e[0] > 0 and n[0] > 0  # east and north are positive
    assert e[0] == pytest.approx(0.2 * 3600 * math.cos(math.radians(10.3)), rel=2e-3)
    back = movers.unproject(e, n, 150.0, 10.0)
    assert np.allclose(back[0], ra, atol=1e-9) and np.allclose(back[1], dec, atol=1e-9)


def test_a_moving_object_is_found_with_its_speed_and_direction():
    # 10 pixels per hour to the right (west) and 4 up (north): 66 arcsec per hour
    cube, frames, grid = field(mover=(15.0, 30.0, 10.0, 4.0, 14.0))
    out = movers.search(cube, frames, grid.wcs, grid.scale_arcsec, trials=2)
    assert len(out["tracks"]) == 1
    t = out["tracks"][0]
    assert t.rate == pytest.approx(math.hypot(10, 4) * SCALE, rel=0.04)
    assert t.v_east == pytest.approx(-10 * SCALE, rel=0.05) and t.v_north == pytest.approx(4 * SCALE, rel=0.08)
    assert t.n_times >= 12 and t.n_visits >= 4 and t.rms_arcsec < 3.0
    assert t.extra["times_seen"] >= 0.9 * t.extra["times_covered"]
    assert t.speed_class() == "very fast"
    assert out["stats"]["chance_tracks"] == 0
    # where it says the object was is where it was put
    ra, dec = t.at(frames[0]["mjd"])
    x, y = grid.wcs.world_to_pixel_values(float(ra), float(dec))
    assert (float(x), float(y)) == pytest.approx((15.0, 30.0), abs=1.0)


def test_a_field_of_stars_and_noise_has_no_moving_object():
    cube, frames, grid = field(mover=None, seed=5)
    out = movers.search(cube, frames, grid.wcs, grid.scale_arcsec)
    assert out["tracks"] == []
    assert out["stats"]["frames"] == len(frames)


def test_shuffling_the_exposure_times_destroys_a_real_track():
    cube, frames, grid = field(mover=(15.0, 30.0, 10.0, 4.0, 14.0))
    dets, sky, _ = movers.detect(cube, frames, grid.wcs, grid.scale_arcsec)
    assert movers.candidates(dets, cube, frames, grid.wcs, sky, grid.scale_arcsec)[0]
    assert movers.chance_tracks(dets, cube, frames, grid.wcs, sky, grid.scale_arcsec, trials=3) == 0


def test_what_comes_back_at_the_same_place_is_not_a_mover():
    d = [movers.Detection(exposure=f"A{k}", mjd=61000.0 + k * 0.4, ra=150.0, dec=10.0, snr=9, flux_mjy=1, npix=3) for k in range(5)]
    passing = [movers.Detection(exposure=f"B{k}", mjd=61000.0 + k * 0.01, ra=150.01 + k * 0.004, dec=10.0, snr=9, flux_mjy=1,
                                npix=3) for k in range(5)]
    kept = movers.drop_recurring(d + passing)
    assert {k.exposure for k in kept} == {p.exposure for p in passing}


def test_four_detections_on_a_line_are_not_yet_an_object():
    c = {"times_seen": 4, "times_covered": 5}
    assert not movers.confirmed(c)
    assert movers.confirmed({"times_seen": 7, "times_covered": 9})
    assert not movers.confirmed({"times_seen": 7, "times_covered": 20})  # seen at too few of the times it could be


def test_pieces_of_one_path_are_joined():
    def piece(t0, n, shift=0.0):
        return movers._track_of([movers.Detection(exposure=f"P{t0}-{k}", mjd=61000.0 + (t0 + k) / 24.0,
                                                  ra=150.0 + (t0 + k) * 60.0 / 3600.0 / math.cos(math.radians(10.0)),
                                                  dec=10.0 + shift / 3600.0, snr=10, flux_mjy=1, npix=3) for k in range(n)])

    a, b = piece(0, 5), piece(6, 5, shift=8.0)  # the same 60 arcsec per hour, 8 arcsec to the side
    assert movers.same_object(a, b)
    joined = movers.merge([a, b])
    assert len(joined) == 1 and len(joined[0].detections) == 10
    other = piece(0, 5, shift=120.0)  # the same motion two arcminutes away is another object
    assert not movers.same_object(a, other)
    assert len(movers.merge([a, other])) == 2


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return mover_store


def found(grid_ra=150.0, shift_h=0.0, n=8):
    dets = [movers.Detection(exposure=f"E{int(shift_h)}-{k}:D2", mjd=61000.0 + (shift_h + k) / 24.0,
                             ra=grid_ra + (shift_h + k) * 40.0 / 3600.0, dec=10.0, snr=12, flux_mjy=2.0, npix=4, detector=2,
                             wave_um=1.2) for k in range(n)]
    t = movers._track_of(dets)
    t.extra.update(covered=n, seen=n, seen_fraction=1.0, times_seen=n, times_covered=n, points=[
        {"mjd": d.mjd, "exposure": d.exposure, "detector": 2, "wave_um": 1.2, "ra": d.ra, "dec": d.dec, "snr": 12.0,
         "flux_mjy": 2.0, "on_static": False, "counted": True} for d in dets])
    return {"detections": dets, "tracks": [t], "stats": {"frames": 100, "detections": n, "tracks": 1}, "version": movers.VERSION}


def test_an_object_keeps_its_id_and_grows_when_a_neighbouring_field_finds_more_of_it(store):
    ids = store.save_field("fieldA", 150.0, 10.0, 50.4, 100, found(shift_h=0.0), {0: {"checked": True, "name": "5 Astraea"}})
    assert len(ids) == 1 and ids[0].startswith("VS-61000-")
    first = store.get(ids[0])
    assert first["n_detections"] == 8 and first["known"]["name"] == "5 Astraea" and first["fields"] == ["fieldA"]
    # the next field holds the continuation of the same path
    again = store.save_field("fieldB", 150.3, 10.0, 50.4, 100, found(shift_h=6.0))
    assert again == ids
    both = store.get(ids[0])
    assert both["n_detections"] == 16 and both["fields"] == ["fieldA", "fieldB"] and both["covered"] == 16
    assert both["known"]["name"] == "5 Astraea"  # a name once found is kept
    assert both["rate_arcsec_hr"] == pytest.approx(40.0 * math.cos(math.radians(10.0)), rel=0.02)
    assert store.count() == 1
    assert store.searched("fieldA", 100) and not store.searched("fieldA", 130) and not store.searched("other", 10)
    # detections of field A are offered to a search next to it, not to its own
    near = store.detections_around(150.0, 10.0, 1.0, not_field="fieldA")
    assert {d.exposure[:2] for d in near} == {"E6"}
    assert "icrs012-v1-1250-0833" in store.coverage()["cells"]
    listed = store.listing(150.0, 10.0, 1.0)
    assert [m["id"] for m in listed] == ids and "detections" not in listed[0]


def test_one_voice_per_browser_and_a_few_per_address(store):
    (mid,) = store.save_field("fieldA", 150.0, 10.0, 50.4, 100, found())
    assert store.vote(mid, "browser-0000000001", "real", "addr1") == {"real": 1, "false": 0, "verdict": None}
    assert store.vote(mid, "browser-0000000001", "false", "addr1") == {"real": 0, "false": 1, "verdict": None}  # changed, not added
    store.vote(mid, "browser-0000000002", "false", "addr1")
    assert store.vote(mid, "browser-0000000003", "false", "addr1")["verdict"] == "false"
    with pytest.raises(PermissionError):  # a fourth browser behind the same address
        store.vote(mid, "browser-0000000004", "real", "addr1")
    assert store.vote(mid, "browser-0000000005", "real", "addr2")["real"] == 1
    assert store.vote(mid, "browser-0000000001", None, "addr1")["false"] == 2  # taken back
    assert store.get(mid, "browser-0000000005")["my_vote"] == "real"
    with pytest.raises(KeyError):
        store.vote("VS-00000-AAAA", "browser-0000000001", "real", "addr1")
    with pytest.raises(ValueError):
        store.vote(mid, "browser-0000000001", "maybe", "addr1")


def test_the_log_and_the_votes_over_http(store):
    from fastapi.testclient import TestClient

    from sphereblink import main

    client = TestClient(main.app)
    assert client.get("/api/movers").json()["movers"] == []
    (mid,) = store.save_field("fieldA", 150.0, 10.0, 50.4, 100, found())
    log = client.get("/api/movers", params={"ra": 150.0, "dec": 10.0, "radius": 2, "coverage": True}).json()
    assert log["total"] == 1 and log["movers"][0]["id"] == mid and log["coverage"]["fields"] == 1
    assert client.get("/api/movers", params={"ids": f"{mid},nonsense"}).json()["movers"][0]["id"] == mid
    one = client.get(f"/api/movers/{mid}").json()
    assert len(one["detections"]) == 8 and one["votes"] == {"real": 0, "false": 0, "verdict": None}
    assert client.get("/api/movers/VS-61000-ZZZZ").status_code == 404
    assert client.get("/api/movers/whatever").status_code == 404
    voter = "3f0c9a52-6f0e-4d0b-9a8e-1c2d3e4f5a6b"
    r = client.post(f"/api/movers/{mid}/vote", json={"voter": voter, "vote": "real"})
    assert r.status_code == 200 and r.json()["votes"]["real"] == 1
    assert client.get(f"/api/movers/{mid}", params={"voter": voter}).json()["my_vote"] == "real"
    assert client.post(f"/api/movers/{mid}/vote", json={"voter": "x", "vote": "real"}).status_code == 400
    assert client.post(f"/api/movers/{mid}/vote", json={"voter": voter, "vote": "perhaps"}).status_code == 400
    assert client.post("/api/movers/VS-61000-ZZZZ/vote", json={"voter": voter, "vote": "real"}).status_code == 404
    # a field that was never opened is not searched because somebody asks about it
    f = client.get("/api/movers/field", params={"ra": 20.0, "dec": -30.0}).json()
    assert f["status"] == "not_loaded" and f["movers"] == []
    assert client.get("/api/movers/field").status_code == 400


JPL = {
    "fields_second": ["Object name", "Astrometric RA (hh:mm:ss)", "Astrometric Dec (dd mm'ss\")", "Dist. from center RA (\")",
                      "Dist. from center Dec (\")", "Dist. from center Norm (\")", "Visual magnitude (V)", "RA rate (\"/h)",
                      "Dec rate (\"/h)"],
    "data_second_pass": [["5 Astraea (A845 XA)", "16:32:59.17", "-16 35'44.8\"", "-428.", "1.E3", "1517.", "12.0", "1.208E+01",
                          "-8.391E+00"],
                         ["69598 (1998 FT18)", "16:35:17.76", "-16 56'58.8\"", "2.E3", "181.", "1571.", "19.5", "3.587E+01",
                          "-9.449E+00"]],
}
SKYBOT = [{"Num": 5, "Name": "Astraea", "RA (hms)": "16 32 59.1719", "DEC (dms)": "-16 35 44.455", "Class": "MB>Middle",
           "VMag (mag)": 11.8, "dRA (arcsec/h)": 12.0828, "dDEC (arcsec/h)": -8.3911},
          {"Name": "broken"}]


def test_both_catalogue_services_are_read_the_same_way():
    a = known_objects.parse_jpl(JPL, "asteroid")
    b = known_objects.parse_skybot(SKYBOT)
    assert len(a) == 2 and len(b) == 1
    assert a[0]["name"] == "5 Astraea (A845 XA)" and b[0]["name"] == "5 Astraea"
    for o in (a[0], b[0]):
        assert o["ra"] == pytest.approx(248.24655, abs=1e-4) and o["dec"] == pytest.approx(-16.59578, abs=1e-4)
        assert o["v_east"] == pytest.approx(12.08, abs=0.01) and o["v_north"] == pytest.approx(-8.39, abs=0.01)
    assert known_objects.parse_jpl({"warning": "no matching records"}, "comet") == []
    assert known_objects._calendar(60897.029005) == "2025-08-10_00:41:46"
    assert known_objects._dashed(248.3708, True) == "16-33-29" and known_objects._dashed(-17.0, False) == "M17-00-00"


def test_a_track_is_named_only_when_place_and_motion_agree():
    objects = known_objects.parse_jpl(JPL, "asteroid")
    mjd = 60897.029

    def track(ra, dec, ve, vn, hours_later=0.0):
        return movers.Track(detections=[], mjd0=mjd + hours_later / 24.0, ra0=ra, dec0=dec, v_east=ve, v_north=vn,
                            rms_arcsec=1.0, n_times=8, n_visits=3)

    astraea = objects[0]
    assert known_objects.match(track(astraea["ra"], astraea["dec"] + 5 / 3600, 12.5, -8.0), objects, mjd)["name"].startswith("5 Astraea")
    # five hours later it has moved on by its rate: still the same object
    later = movers.unproject(12.08 * 5, -8.39 * 5, astraea["ra"], astraea["dec"])
    assert known_objects.match(track(float(later[0]), float(later[1]), 12.0, -8.4, hours_later=5.0), objects, mjd) is not None
    assert known_objects.match(track(astraea["ra"], astraea["dec"], -30.0, 20.0), objects, mjd) is None  # right place, other motion
    assert known_objects.match(track(astraea["ra"] + 0.1, astraea["dec"], 12.0, -8.4), objects, mjd) is None  # right motion, other place


def test_not_known_is_never_said_without_having_asked(monkeypatch):
    t = movers.Track(detections=[], mjd0=60897.0, ra0=248.2, dec0=-16.6, v_east=12.0, v_north=-8.0, rms_arcsec=1.0, n_times=8,
                     n_visits=3)
    monkeypatch.setattr(known_objects, "predicted", lambda *a, **k: ([], ""))
    assert known_objects.identify([t]) == {0: {"checked": False}}
    monkeypatch.undo()
    monkeypatch.setattr(known_objects, "SOURCES", (("JPL", lambda *a: []), ("SkyBoT", lambda *a: [])))
    assert known_objects.identify([t]) == {0: {"checked": True, "by": "JPL + SkyBoT", "name": None}}


def test_what_one_service_does_not_list_is_asked_of_the_other(monkeypatch):
    # 3I/ATLAS on 2025-12-14 as SkyBoT gives it; JPL's identification has no record of it
    comet = known_objects.parse_skybot([{"Name": "3I", "RA (hms)": "11 08 29.7060", "DEC (dms)": "+05 10 33.178", "Class": "Comet",
                                         "VMag (mag)": 0, "dRA (arcsec/h)": -165.2815, "dDEC (arcsec/h)": 57.8071}])
    found = movers.Track(detections=[], mjd0=61023.349, ra0=167.0999, dec0=5.1835, v_east=-165.4, v_north=57.0, rms_arcsec=7.7,
                         n_times=20, n_visits=5)
    asked = []
    monkeypatch.setattr(known_objects, "SOURCES", (("JPL", lambda *a: asked.append("JPL") or []),
                                                   ("SkyBoT", lambda *a: asked.append("SkyBoT") or comet)))
    out = known_objects.identify([found])
    assert asked == ["JPL", "SkyBoT"]
    assert out[0]["name"] == "3I" and out[0]["kind"] == "comet" and out[0]["by"] == "SkyBoT" and out[0]["sep_arcsec"] < 120

    def down(*a):
        raise OSError("no answer")

    monkeypatch.setattr(known_objects, "SOURCES", (("JPL", down), ("SkyBoT", lambda *a: comet)))
    assert known_objects.identify([found])[0]["name"] == "3I"  # one service down: the other still names it


def test_free_memory_is_read_from_the_container_limit(tmp_path):
    from sphereblink import movers_api

    assert movers_api.free_mb(tmp_path) is None  # no limit to read: the search is not held back
    (tmp_path / "memory.max").write_text("8000000000\n")
    (tmp_path / "memory.current").write_text("6500000000\n")
    (tmp_path / "memory.stat").write_text("anon 4000000000\ninactive_file 1000000000\nactive_file 500000000\n")
    assert movers_api.free_mb(tmp_path) == 3000.0  # the file cache counts as free
    (tmp_path / "memory.max").write_text("max\n")
    assert movers_api.free_mb(tmp_path) is None
