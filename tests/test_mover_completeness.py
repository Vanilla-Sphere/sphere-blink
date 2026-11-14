"""Injection and recovery for the blind search: the fakes and the counting, everything offline."""

import math

import numpy as np
import pytest

from sphereblink import mover_completeness as mc
from sphereblink import movers
from sphereblink.cutout import Grid

SCALE = 6.15


def field(n=121, seed=1, stars=25, orbits=9, burst=4, start=61000.0):
    """Noise and stars that stay, seen as SPHEREx sees a field: a few exposures two minutes apart on every
    orbit, two detectors at once."""
    rng = np.random.default_rng(seed)
    grid = Grid(ra=150.0, dec=10.0, npix=n, scale_arcsec=SCALE)
    yy, xx = np.mgrid[0:n, 0:n]
    sky = np.zeros((n, n))
    for _ in range(stars):
        x, y, a = rng.uniform(5, n - 5), rng.uniform(5, n - 5), rng.uniform(8, 300)
        sky += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8**2))
    frames, images = [], []
    for o in range(orbits):
        for k in range(burst):
            t = start + o * 96.0 / 1440.0 + k * 2.2 / 1440.0
            for det, colour in ((2, 1.0), (5, 0.6)):
                images.append((sky * colour + rng.normal(0, 1, (n, n))).astype(np.float32))
                frames.append({"exposure": f"E{o:02d}{k}:D{det}", "mjd": t, "detector": det, "wave_um": 1.2 if det == 2 else 4.0})
    return np.stack(images), frames, grid


def magnitude(peak_mjy_sr: float, wave_um: float = 1.2) -> float:
    """The V magnitude of a sun-coloured fake whose brightest pixel adds about ``peak_mjy_sr`` at ``wave_um``."""
    to_mjy = (SCALE / movers.ARCSEC) ** 2 * 1e9
    flux = peak_mjy_sr * to_mjy / 0.31  # the brightest pixel holds about 0.31 of the light in detectors 1 to 3
    return float(-2.5 * math.log10(flux / mc.flux_mjy(0.0, wave_um)))


def test_brightness_follows_the_magnitude_and_the_spectrum():
    assert mc.flux_mjy(0.0, 0.55, "flat") == pytest.approx(3631e3)
    assert mc.flux_mjy(20.0, 3.0, "flat") == pytest.approx(3631e3 * 1e-8)
    # sunlight is brighter at 1.24 micron than in V by 0.26 magnitude (AB), and much fainter at 4.6 micron
    assert mc.flux_mjy(18.0, 1.24) / mc.flux_mjy(18.0, 1.24, "flat") == pytest.approx(10 ** (0.4 * 0.26))
    assert mc.flux_mjy(18.0, 4.62) / mc.flux_mjy(18.0, 1.24) == pytest.approx(10 ** (-0.4 * (1.77 + 0.26)))
    assert mc.flux_mjy(18.0, 9.0) == mc.flux_mjy(18.0, 5.2)  # outside the table: the nearest value, no extrapolation
    with pytest.raises(ValueError):
        mc.flux_mjy(18.0, 1.0, "blue")


def test_a_fake_holds_all_its_light_and_looks_like_a_star_of_the_frames():
    rng = np.random.default_rng(3)
    oy, ox = np.mgrid[-4:5, -4:5]
    for sigma, share in ((mc.PSF_SIGMA[2], 0.31), (mc.PSF_SIGMA[5], 0.255)):
        peaks, sums = [], []
        for _ in range(400):
            x, y = rng.uniform(-0.5, 0.5, 2)
            s = mc.stamp(ox - x, oy - y, sigma, rng.uniform(0, 2 * math.pi), tuple(rng.uniform(-0.5, 0.5, 2)))
            assert s.min() >= 0
            peaks.append(s.max())
            sums.append(s.sum())
        # resampling keeps the light on average, not exposure by exposure: up to a tenth either way
        assert np.mean(sums) == pytest.approx(1.0, abs=0.005) and np.ptp(sums) < 0.25
        # measured on the stars of the test fields: median 0.31 (detectors 1 to 3) and 0.25 to 0.26 (4 to 6)
        assert np.median(peaks) == pytest.approx(share, abs=0.02)


def test_a_fast_fake_leaves_a_trail():
    oy, ox = np.mgrid[-4:5, -4:5]
    still = mc.stamp(ox, oy, 0.4, 0.3, (0.1, -0.2))
    trailed = mc.stamp(ox, oy, 0.4, 0.3, (0.1, -0.2), trail=(3.0, 0.0))
    assert trailed.sum() == pytest.approx(still.sum(), abs=0.02)
    assert trailed.max() < 0.6 * still.max()
    assert trailed[4, 3:6].sum() > 2 * trailed[3:6, 4].sum() - trailed[4, 4]  # along x, not along y


def test_fakes_go_where_they_are_told_and_nowhere_else():
    cube, frames, grid = field(stars=0)
    cube[:, 60, 70] = np.nan  # a pixel the exposures do not have
    before = cube.copy()
    ra, dec = grid.wcs.pixel_to_world_values(40.0, 50.0)
    fake = mc.Fake(ra0=float(ra), dec0=float(dec), mjd0=frames[0]["mjd"], v_east=-30.0, v_north=0.0, v_mag=16.0)
    other_pass = mc.Fake(ra0=float(ra), dec0=float(dec), mjd0=frames[0]["mjd"] + 200.0, v_east=5.0, v_north=5.0, v_mag=12.0)
    told = mc.inject(cube, frames, grid.wcs, grid.scale_arcsec, [fake, other_pass], seed=5)
    assert told[1] == []  # no exposure in the pass of that time: nothing added
    assert len(told[0]) == len(frames) and all(s["observable"] for s in told[0])
    added = np.nan_to_num(cube - before)
    to_mjy = (SCALE / movers.ARCSEC) ** 2 * 1e9
    for s in told[0]:
        got = added[s["frame"]]
        assert got.sum() * to_mjy == pytest.approx(mc.flux_mjy(16.0, s["wave_um"]), rel=0.12)  # all its light, at this wavelength
        y, x = np.unravel_index(got.argmax(), got.shape)
        assert abs(x - s["x"]) <= 1.5 and abs(y - s["y"]) <= 1.5
    total = sum(added[s["frame"]].sum() * to_mjy / mc.flux_mjy(16.0, s["wave_um"]) for s in told[0]) / len(told[0])
    assert total == pytest.approx(1.0, abs=0.01)
    # it moves west (to the right) at 30 arcsec per hour
    hours = (told[0][-1]["mjd"] - told[0][0]["mjd"]) * 24.0
    assert told[0][-1]["x"] - told[0][0]["x"] == pytest.approx(30.0 * hours / SCALE, abs=0.1)
    assert np.isnan(cube[:, 60, 70]).all()  # a missing pixel stays missing
    # the same seed gives the same frames
    again = before.copy()
    mc.inject(again, frames, grid.wcs, grid.scale_arcsec, [fake], seed=5)
    assert np.array_equal(np.nan_to_num(again), np.nan_to_num(cube))


def test_a_frame_without_a_wavelength_gives_the_brightness_of_its_band():
    """A frame that does not cover the centre of the field gives NaN for its wavelength (one in three of the real
    ones). The fake is then as bright as in the middle of that detector's band, not as at 1 micron."""
    cube, frames, grid = field(stars=0, orbits=3)
    for f in frames[::2]:
        f["wave_um"] = float("nan") if f["detector"] == 2 else None
    before = cube.copy()
    ra, dec = grid.wcs.pixel_to_world_values(40.0, 50.0)
    fake = mc.Fake(ra0=float(ra), dec0=float(dec), mjd0=frames[0]["mjd"], v_east=-30.0, v_north=0.0, v_mag=16.0)
    told = mc.inject(cube, frames, grid.wcs, grid.scale_arcsec, [fake], seed=5)
    added = np.nan_to_num(cube - before)
    to_mjy = (SCALE / movers.ARCSEC) ** 2 * 1e9
    assert len(told[0]) == len(frames)
    for s, f in zip(told[0], frames):
        known = f["wave_um"] is not None and np.isfinite(f["wave_um"])
        assert s["wave_um"] == (f["wave_um"] if known else mc.BAND_UM[f["detector"]])
        assert added[s["frame"]].sum() * to_mjy == pytest.approx(mc.flux_mjy(16.0, s["wave_um"]), rel=0.12)
    # in the long bands that is several times fainter than at 1 micron
    assert mc.flux_mjy(16.0, mc.BAND_UM[5]) < 0.3 * mc.flux_mjy(16.0, 1.0)


def test_a_bright_fake_is_found_and_a_faint_one_is_not():
    cube, frames, grid = field()
    wcs = grid.wcs
    noise = mc.noise_maps(cube, frames)
    assert np.nanmedian(noise) == pytest.approx(1.0, rel=0.25)  # the field was made with noise 1
    a = wcs.pixel_to_world_values(20.0, 30.0)
    b = wcs.pixel_to_world_values(90.0, 80.0)
    t0 = frames[0]["mjd"]
    bright = mc.Fake(ra0=float(a[0]), dec0=float(a[1]), mjd0=t0, v_east=-40.0, v_north=20.0, v_mag=magnitude(14.0), label=1)
    faint = mc.Fake(ra0=float(b[0]), dec0=float(b[1]), mjd0=t0, v_east=25.0, v_north=-30.0, v_mag=magnitude(1.5), label=2)
    out = mc.trial(cube, frames, wcs, grid.scale_arcsec, [bright, faint], seed=2, noise=noise, baseline=[])
    one, two = out["fakes"]
    assert one["found"] and one["strong"] and one["sep_arcsec"] < 6 and abs(one["rate_error"]) < 2
    assert one["times"] == 36 and one["snr"] > 8 and one["snr_detector"] == 2 and one["detected_times"] >= 30
    assert not two["found"] and not two["strong"] and two["snr"] < 3 and two["detected_times"] == 0
    assert out["others"] == [] and out["baseline_found"] == 0
    assert np.isfinite(cube).all() and cube.max() < 400  # the frames given are not changed


def test_any_search_with_the_same_input_is_measured():
    cube, frames, grid = field(stars=0, orbits=3)
    ra, dec = grid.wcs.pixel_to_world_values(60.0, 60.0)
    fake = mc.Fake(ra0=float(ra), dec0=float(dec), mjd0=frames[0]["mjd"], v_east=10.0, v_north=0.0, v_mag=15.0)
    seen = {}

    def finds_everything(cube, frames, wcs, scale):
        seen["frames"] = len(frames)
        t = movers.Track(detections=[], mjd0=fake.mjd0 + 0.1, ra0=float(fake.at(fake.mjd0 + 0.1)[0]), dec0=float(fake.at(fake.mjd0 + 0.1)[1]),
                         v_east=10.5, v_north=0.4, rms_arcsec=1.0, n_times=9, n_visits=3,
                         extra={"times_seen": 9, "times_covered": 10, "covered": 20, "seen": 18, "visits_seen": 3})
        stray = movers.Track(detections=[], mjd0=fake.mjd0, ra0=150.2, dec0=10.1, v_east=80.0, v_north=0.0, rms_arcsec=1.0,
                             n_times=4, n_visits=2)
        return {"tracks": [t, stray], "detections": [], "stats": {}}

    out = mc.trial(cube, frames, grid.wcs, grid.scale_arcsec, [fake], search=finds_everything)
    assert seen["frames"] == len(frames)
    assert out["fakes"][0]["found"] and out["fakes"][0]["strong"]
    assert len(out["others"]) == 1 and out["others"][0]["rate"] == 80.0  # a path that is no fake is a false alarm


def test_random_fakes_cover_the_ranges_asked_for():
    cube, frames, grid = field(stars=0, orbits=3)
    fakes = mc.draw(frames, grid.wcs, grid.npix, 300, np.random.default_rng(1), v_mag=(16.0, 19.0), rate=(2.0, 200.0))
    rates = np.array([f.rate for f in fakes])
    assert rates.min() >= 2.0 and rates.max() <= 200.0 and 15 < np.median(rates) < 27  # uniform in the logarithm: median 20
    assert all(16.0 <= f.v_mag <= 19.0 for f in fakes)
    quadrants = np.bincount((np.array([f.pa_deg for f in fakes]) // 90).astype(int), minlength=4)
    assert quadrants.min() > 50
    times = {round(f["mjd"], 6) for f in frames}
    assert all(round(f.mjd0, 6) in times for f in fakes)


def test_the_numbers():
    lo, hi = mc.wilson(9, 10)
    assert lo < 0.9 < hi and hi < 1.0 and lo > 0.7
    assert mc.wilson(0, 0) == (0.0, 1.0)
    assert mc.upper_limit(0, 32) == pytest.approx(0.0936, abs=0.001)  # nothing in 32 searches: fewer than 0.094 per search
    rng = np.random.default_rng(4)
    rows = []
    for _ in range(3000):
        v, rate = rng.uniform(15, 20), rng.uniform(5, 50)
        p = 0.9 / (1 + math.exp((v - 18.2) / 0.2))
        rows.append({"v_mag": v, "rate": rate, "times": 20, "found": rng.random() < p, "strong": False})
    fit = mc.half_point(rows)
    assert fit["plateau"] == pytest.approx(0.9, abs=0.03) and fit["at"] == pytest.approx(18.2 + 0.2 * math.log(0.9 / 0.5 - 1), abs=0.08)
    cells = mc.table(rows, "v_mag", [15, 17, 19, 20], [5, 50])
    assert [c["n"] > 0 for c in cells] == [True] * 3 and cells[0]["share"] == pytest.approx(0.9, abs=0.04)
    assert cells[2]["share"] < 0.05 and cells[0]["low"] < cells[0]["share"] < cells[0]["high"]
    assert mc.half_point(rows[:10]) is None


def test_the_result_file_of_the_viewer_holds_together():
    import json
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "web"
    page = (web / "index.html").read_text()
    assert "completeness.js" in page and "completeness.css" in page and "data-completeness" in page
    d = json.loads((web / "data" / "mover-completeness.json").read_text())
    assert d["all"]["fakes"] >= 1000 and set(d["fields"]) == {"sparse", "medium", "crowded"}
    # a later search that gave the same on some of the rounds is named with the number of those rounds
    same = d.get("same_on")
    assert same is None or (same["version"] > d["version"] and 0 < same["rounds"] <= d["all"]["rounds"])
    for part in [d["all"], *d["fields"].values()]:
        assert part["observable"] <= part["fakes"]
        for level in ("found", "strong"):
            for c in part[level]["curve"]:
                assert 0 <= c["found"] <= c["n"] and (c["share"] is None or c["low"] <= c["share"] <= c["high"])
            assert len(part[level]["grid"]) == len(d["rate_edges"]) - 1
            assert all(len(row) == len(d["v_coarse"]) - 1 for row in part[level]["grid"])
        # the stronger evidence is never met more often than the weaker
        assert sum(c["found"] for c in part["strong"]["curve"]) <= sum(c["found"] for c in part["found"]["curve"])
    # bright fakes are mostly found, the faintest are not: the measurement spans the limit
    curve = d["all"]["found"]["curve"]
    assert curve[0]["share"] > 0.7 and curve[-1]["share"] < 0.05
    # the second search (shift and stack) on its own fakes: the parts add up to the whole
    stack = d.get("stack")
    if stack:
        whole = stack["all"]
        assert whole["strong"] <= whole["found"] <= whole["either"] <= whole["fakes"] and whole["bright_found"] <= whole["bright"] <= whole["fakes"]
        for key in ("fakes", "found", "strong", "bright", "bright_found"):
            assert sum(f[key] for f in stack["fields"].values()) == whole[key]
            assert sum(part[key] for part in whole["passes"].values()) == whole[key]
        for key in ("fakes", "found", "strong"):
            assert sum(row[key] for row in whole["by_v"]) == whole[key]
        assert whole["distance"]["holds"] <= whole["distance"]["given"] <= whole["found"]
        assert "cpStackWords" in (web / "completeness.js").read_text()


def test_an_asteroid_glows_where_the_long_bands_look():
    # below 3 micron an asteroid of the main belt is reflected sunlight; at 4.6 micron its own heat is most of it
    assert mc.heat_share(1.2) < 1e-6 and mc.heat_share(3.0) < 0.02
    assert 2.0 < mc.heat_share(4.6) < 6.0 and mc.heat_share(5.0) > mc.heat_share(4.6) > mc.heat_share(4.0) > 0.2
    assert mc.flux_mjy(18.0, 1.2, "asteroid") == pytest.approx(mc.flux_mjy(18.0, 1.2, "sun"), rel=1e-5)
    assert mc.flux_mjy(18.0, 4.6, "asteroid") / mc.flux_mjy(18.0, 4.6, "sun") == pytest.approx(1 + mc.heat_share(4.6))
    # closer to the Sun it is warmer, and a dark body reflects less: both give heat a larger share
    assert mc.heat_share(4.6, r_au=2.2) > mc.heat_share(4.6) > mc.heat_share(4.6, r_au=3.2)
    assert mc.heat_share(4.6, albedo=0.05) > mc.heat_share(4.6)


def _far():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "mover_completeness_far.py"
    if "mover_completeness_far" not in sys.modules:
        spec = importlib.util.spec_from_file_location("mover_completeness_far", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mover_completeness_far"] = module
        spec.loader.exec_module(module)
    return sys.modules["mover_completeness_far"]


def test_a_distant_fake_moves_as_earth_makes_it():
    far = _far()
    from sphereblink import mover_stack as ms

    mjd0 = 61000.0
    still = far.FarFake(ra0=167.0, dec0=5.0, mjd0=mjd0, v_east=0.0, v_north=0.0, v_mag=18.0, spectrum="sun", label=0, gamma=1 / 40.0)
    assert still.at(mjd0) == pytest.approx((167.0, 5.0), abs=1e-9)
    # with no motion of its own, its path is Earth's, seen from 40 au
    mjd = mjd0 + np.array([2.0, 6.0, 12.0])
    e, n = ms.earth_offsets(np.concatenate([[mjd0], mjd]), 167.0, 5.0)
    east, north = still.offsets(mjd)
    assert east == pytest.approx((e[1:] - e[0]) / 40.0 * far.RAD) and north == pytest.approx((n[1:] - n[0]) / 40.0 * far.RAD)
    # Earth moves at 30 km/s at most across the line of sight: 3.1 arcsec per hour from 40 au
    assert 0.0 < still.rate < 3.2
    # twice as far, half the path; and the path bends, which a straight line of the same ends does not
    half = far.FarFake(ra0=167.0, dec0=5.0, mjd0=mjd0, v_east=0.0, v_north=0.0, v_mag=18.0, spectrum="sun", label=1, gamma=1 / 80.0)
    assert np.hypot(*half.offsets(mjd)) == pytest.approx(np.hypot(east, north) / 2.0)
    assert abs(np.hypot(east[1], north[1]) - np.hypot(east[2], north[2]) / 2.0) > 0.5
    # its own motion adds a straight line
    own = far.FarFake(ra0=167.0, dec0=5.0, mjd0=mjd0, v_east=0.3, v_north=-0.2, v_mag=18.0, spectrum="sun", label=2, gamma=1 / 40.0)
    assert own.offsets(mjd)[0] - east == pytest.approx(0.3 * (mjd - mjd0) * 24.0)
    assert own.offsets(mjd)[1] - north == pytest.approx(-0.2 * (mjd - mjd0) * 24.0)
    # the draw keeps the own motion inside what a closed orbit allows, and the distance inside its range
    wcs = Grid(ra=167.0, dec=5.0, npix=488, scale_arcsec=6.15).wcs
    for fake in far.draw([{"mjd": mjd0 + k} for k in range(5)], wcs, 488, 20, np.random.default_rng(3)):
        assert far.AU[0] <= 1 / fake.gamma <= far.AU[1]
        assert math.hypot(fake.v_east, fake.v_north) <= far.OWN_MOTION * ms.ESCAPE * fake.gamma**1.5 + 1e-12


def test_a_path_that_leaves_its_fake_is_told_from_a_false_one():
    far = _far()
    mjd0 = 61000.0
    fake = far.FarFake(ra0=167.0, dec0=5.0, mjd0=mjd0, v_east=0.1, v_north=0.2, v_mag=18.0, spectrum="sun", label=4, gamma=1 / 50.0)
    other = far.FarFake(ra0=167.2, dec0=5.1, mjd0=mjd0, v_east=0.0, v_north=0.0, v_mag=18.0, spectrum="sun", label=5, gamma=1 / 90.0)
    mjd = mjd0 + np.linspace(0.0, 12.0, 12)
    ra, dec = fake.at(mjd)

    def track(at: float, shift_deg: np.ndarray, dv: float = 0.0) -> movers.Track:
        r0, d0 = fake.at(at)
        ve, vn = fake.velocity(at)
        points = [{"mjd": float(t), "ra": float(r), "dec": float(d + s)} for t, r, d, s in zip(mjd, ra, dec, shift_deg)]
        return movers.Track(detections=[], mjd0=at, ra0=r0, dec0=d0, v_east=ve + dv, v_north=vn, rms_arcsec=0.5, n_times=12, n_visits=4,
                            extra={"points": points})

    good = track(mjd0 + 6.0, np.zeros(12))
    assert far.match(good, fake)[0] and not far.match(good, other)[0]
    assert far.on_fakes(good, [other, fake]) == {"label": 4, "share": 1.0, "median_arcsec": 0.0, "first_arcsec": 0.0, "last_arcsec": 0.0}
    # the right place with another motion is not the fake
    assert not far.match(track(mjd0 + 6.0, np.zeros(12), dv=1.0), fake)[0]
    # a path that follows the fake for nine of its twelve points and is an arcminute off at the end
    leaves = track(mjd0 + 6.0, np.where(np.arange(12) < 9, 0.0, 60.0 / 3600.0))
    on = far.on_fakes(leaves, [other, fake])
    assert on["label"] == 4 and on["share"] == 0.75 and on["first_arcsec"] == 0.0 and on["last_arcsec"] == pytest.approx(60.0, abs=0.1)
    assert far.kind_of({"on_fake": on}) == "follows"
    assert far.kind_of({"on_fake": {**on, "share": 0.4}}) == "crosses"
    assert far.kind_of({"on_fake": far.on_fakes(track(mjd0, np.full(12, 0.05)), [other, fake])}) == "nothing"
    assert far.kind_of({}) == "unknown"


def test_the_counts_of_the_distant_fakes():
    far = _far()

    def fake(v, times=20, found=False, strong=False, linker=False, au=50.0, dist=None):
        row = {"v_mag": v, "times": times, "found": found, "strong": strong, "linker": linker, "au": au}
        if found:
            row["track"] = {"distance": dist or {}}
        return row

    rows = [{"seconds_stack": 100.0, "fakes": [
        fake(17.0, found=True, strong=True, dist={"distance_au": 48.0, "distance_min_au": 40.0, "distance_max_au": 60.0}),
        fake(17.2, found=True, strong=True, linker=True, dist={"distance_au": 20.0, "distance_min_au": 15.0, "distance_max_au": 30.0}),
        fake(19.2, found=True, dist={"distance_au": 90.0, "distance_min_au": 45.0, "distance_max_au": None}),
        fake(19.3, found=True), fake(20.2), fake(17.1, times=5, found=True, strong=True)],
        "others": [{"strong": True, "on_fake": {"share": 0.8}}, {"strong": False, "on_fake": {"share": 0.4}}, {"strong": False}]}]
    t = far.tally(rows)
    assert (t["fakes"], t["found"], t["strong"], t["linker"]) == (5, 4, 2, 1)  # the fake seen at 5 times does not count
    assert t["by_v"][0] == {"v": [16.5, 17.5], "fakes": 2, "found": 2, "strong": 2} and t["by_v"][3]["found"] == 2 and t["by_v"][5]["fakes"] == 1
    assert t["distance"] == {"given": 3, "holds": 2}  # a range without an upper end holds any larger distance
    assert t["others"] == {"follows": {"paths": 1, "strong": 1}, "crosses": {"paths": 1, "strong": 0}, "unknown": {"paths": 1, "strong": 0}}
