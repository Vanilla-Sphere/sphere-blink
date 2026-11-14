"""Shift-and-stack for faint and slow moving objects: everything offline, on made-up frames."""

import math

import numpy as np
import pytest

from sphereblink import mover_stack as ms
from sphereblink import movers
from sphereblink.cutout import Grid

SCALE = 6.15
START = 61000.0  # the made-up field (150, +10) is 90 degrees from the Sun then, as SPHEREx looks


def visit_times(days=12.0, per_day=4, start=START):
    return [start + k / per_day for k in range(int(days * per_day))]


def field(mover=None, n=96, seed=1, stars=12, days=12.0, start=START, gamma=1 / 45.0, second_epoch=True):
    """Noise, stars that stay, and one object ``mover = (x, y, vx, vy, peak)``:
    pixels at the middle of the pass, its own motion in pixels per hour, and
    its peak in sigma of one frame at 1 micron. It is coloured like sunlight
    and seen from the moving Earth as a body at 1/gamma au is: Earth's motion
    is added to its own, and its path bends. Each visit: two exposures, each
    on two detectors. A second epoch half a year later holds the same stars
    and no object."""
    rng = np.random.default_rng(seed)
    grid = Grid(ra=150.0, dec=10.0, npix=n, scale_arcsec=SCALE)
    yy, xx = np.mgrid[0:n, 0:n]
    sky = np.zeros((n, n))
    for _ in range(stars):
        x, y, a = rng.uniform(5, n - 5), rng.uniform(5, n - 5), rng.uniform(8, 200)
        sky += a * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8**2))
    ts = visit_times(days, start=start)
    mid = 0.5 * (ts[0] + ts[-1])
    curve = ms.bend(np.array(ts), 150.0, 10.0, SCALE)
    earth = ms.reflex(np.array(ts), 150.0, 10.0, SCALE)
    frames, images = [], []

    def add(t, k, where=None):
        for det, wave, colour in ((2, 1.2, 1.0), (5, 4.0, 0.6)):
            img = sky * colour + rng.normal(0, 1, (n, n))
            if where is not None:
                img += mover[4] * float(ms.template(wave)) * np.exp(-((xx - where[0]) ** 2 + (yy - where[1]) ** 2) / (2 * 0.8**2))
            images.append(img.astype(np.float32))
            frames.append({"exposure": f"E{len(frames):04d}:D{det}", "mjd": t + k * 2.2 / 1440.0, "detector": det, "wave_um": wave})

    for j, t in enumerate(ts):
        for k in range(2):
            where = None
            if mover is not None:
                h = (t - mid) * 24.0
                where = (mover[0] + (mover[2] + gamma * earth[0]) * h + gamma * curve[j, 0],
                         mover[1] + (mover[3] + gamma * earth[1]) * h + gamma * curve[j, 1])
            add(t, k, where)
    if second_epoch:
        for t in visit_times(days, start=start + 182.0):
            add(t, 0)
    return np.stack(images), frames, grid


def strong(out):
    """Paths that stand without a catalogue name. Weaker ones are kept for the catalogues to confirm, and chance makes some."""
    return [t for t in out["tracks"] if t.extra["stack_strong"]]


def motion(mover, gamma=1 / 45.0, days=12.0):
    """What the object of ``field`` does in the sky: pixels per hour (x, y)."""
    earth = ms.reflex(np.array(visit_times(days)), 150.0, 10.0, SCALE)
    return mover[2] + gamma * earth[0], mover[3] + gamma * earth[1]


def test_reflected_sunlight_fades_towards_long_wavelengths():
    f = ms.template(np.array([1.0, 2.0, 4.5]))
    assert f[0] == pytest.approx(1.0) and f[0] > f[1] > f[2] and f[2] < 0.2
    assert np.all(ms.template(np.array([1.0, 4.5]), "flat") == 1.0)


def test_visits_group_exposures_taken_within_minutes():
    mjd = np.array([61000.0, 61000.0015, 61000.25, 61000.2515, 61000.003])
    groups = ms.visits_of(mjd)
    assert [sorted(g.tolist()) for g in groups] == [[0, 1, 4], [2, 3]]


def test_shuffling_keeps_the_frames_of_a_visit_together_and_stays_within_a_pass():
    frames = [{"mjd": t + k * 0.0015, "exposure": f"{i}{k}"} for i, t in enumerate(visit_times(5.0) + visit_times(5.0, start=START + 180)) for k in range(2)]
    new = ms.shuffled(frames, seed=3)
    old_t, new_t = np.array([f["mjd"] for f in frames]), np.array([f["mjd"] for f in new])
    assert not np.allclose(old_t, new_t)
    assert np.allclose(np.sort(old_t), np.sort(new_t), atol=1e-9)  # the same times, in another order
    assert np.allclose(new_t[1::2] - new_t[0::2], 0.0015, atol=1e-9)  # a visit stays a visit
    assert np.all((new_t < START + 90) == (old_t < START + 90))  # and a pass a pass


def test_the_grid_of_velocities_follows_the_blur_it_may_cause():
    grid = ms.velocity_grid(0.2, 1.0, 0.1)
    speed = np.hypot(grid[:, 0], grid[:, 1])
    assert speed.min() >= 0.2 - 1e-9 and speed.max() <= 1.0 + 1e-9
    assert len(grid) == pytest.approx(math.pi * (1.0 - 0.04) / 0.01, rel=0.1)


def test_the_bend_of_a_path_is_what_earth_does_in_two_weeks():
    mjd = START + np.arange(0, 14.01, 0.25)
    curve = ms.bend(mjd, 150.0, 10.0, SCALE)
    sag = np.hypot(curve[:, 0], curve[:, 1]).max()
    # Earth falls towards the Sun by a t^2 / 2 = 0.0072 au in a week: at 1 au that is 1500 arcsec, less the straight line
    assert 100 < sag < 260
    assert abs(np.polyfit(mjd - mjd.mean(), curve[:, 0], 1)[0]) < 1e-6  # no straight part is left in it


def test_stacking_along_the_right_motion_finds_what_no_single_visit_shows():
    rng = np.random.default_rng(2)
    k, n = 30, 64
    tau = np.linspace(-60, 60, k)
    psi = rng.normal(0, 1, (k, n, n)).astype(np.float32)
    phi = np.ones((k, n, n), dtype=np.float32)
    for i in range(k):  # 1.5 sigma per visit, moving 0.1 pixel per hour to the right
        psi[i, 30, int(round(32 + 0.1 * tau[i]))] += 1.5
    vels = ms.velocity_grid(0.0, 0.2, 0.025)
    best, arg, kappa = ms.stack_search(psi, phi, tau, vels, part_min=0.5)
    y, x = np.unravel_index(int(np.argmax(best)), best.shape)
    assert (x, y) == (32, 30) and best[y, x] > 6.0
    assert tuple(vels[arg[y, x]]) == pytest.approx((0.1, 0.0), abs=0.026)
    assert kappa == pytest.approx(1.0, abs=0.15)  # independent Gaussian noise needs no correction


def test_earth_moves_a_distant_body_and_a_closed_orbit_limits_the_rest():
    mjd = START + np.arange(0, 12.01, 0.25)
    earth = ms.reflex(mjd, 150.0, 10.0, SCALE) * SCALE  # arcsec per hour for a body at 1 au
    assert 5 < math.hypot(*earth) < 150  # Earth moves at 148 arcsec per hour seen from 1 au, mostly along the line of sight here
    vels = np.array([0.02 * earth, 0.02 * earth + [0.3, 0.0], 0.02 * earth + [1.5, 0.0], [0.0, 0.0] if math.hypot(*earth) > 40 else -0.02 * earth])
    ok = ms.bound_velocities(vels, 0.015, 0.025, earth, 0.0)
    # at 50 au: Earth's part alone, and with 0.3 arcsec per hour of its own (escape there: 0.59); not with 1.5
    assert ok[0] and ok[1] and not ok[2]


@pytest.fixture(scope="module")
def faint():
    """An object of 1.7 sigma per frame at 45 au: no frame shows it."""
    mover = (40.0, 52.0, 0.05, -0.03, 1.7)
    cube, frames, grid = field(mover=mover)
    return mover, cube, frames, grid, ms.search(cube, frames, grid.wcs, grid.scale_arcsec)


def test_a_faint_slow_object_is_found_where_it_is(faint):
    mover, cube, frames, grid, out = faint
    assert len(strong(out)) == 1
    t = out["tracks"][0]
    e = t.extra
    vx, vy = motion(mover)
    assert e["method"] == "stack" and e["stack_snr"] >= ms.STRONG_SNR and e["stack_strong"]
    assert t.rate == pytest.approx(math.hypot(vx, vy) * SCALE, rel=0.1)
    assert t.v_east == pytest.approx(-vx * SCALE, abs=0.08) and t.v_north == pytest.approx(vy * SCALE, abs=0.08)
    mid = START + 0.5 * (12.0 - 0.25)
    ra, dec = t.at(mid)
    x, y = grid.wcs.world_to_pixel_values(float(ra), float(dec))
    assert (float(x), float(y)) == pytest.approx((40.0, 52.0), abs=1.5)
    assert e["visits_seen"] >= ms.MIN_VISITS_SEEN and e["stack_visits_positive"] >= 0.7 * e["stack_visits"]
    assert e["stack_stamp"]["ok"] and len(e["points"]) == e["covered"] and e["times_covered"] >= ms.MIN_TIMES
    assert out["stats"]["own_sky"] == 2  # the sky of each pass was made from the other epoch
    d = e["stack_distance"]
    assert d["distance_min_au"] <= 45.0 and (d["distance_max_au"] is None or d["distance_max_au"] >= 45.0)


def test_the_linker_alone_does_not_find_it(faint):
    mover, cube, frames, grid, _ = faint
    assert movers.search(cube, frames, grid.wcs, grid.scale_arcsec, trials=0)["tracks"] == []


def test_with_the_times_shuffled_nothing_is_found(faint):
    mover, cube, frames, grid, out = faint
    assert ms.chance_tracks(cube, frames, grid.wcs, grid.scale_arcsec, trials=2, real=out["tracks"]) == 0


def test_stars_and_noise_give_no_object():
    cube, frames, grid = field(mover=None, seed=4)
    out = ms.search(cube, frames, grid.wcs, grid.scale_arcsec)
    assert strong(out) == [] and out["stats"]["passes"] == 2


def test_something_that_does_not_move_is_not_reported():
    # a source that is there during the pass and gone half a year later: a variable star, not a moving object
    cube, frames, grid = field(mover=(40.0, 52.0, 0.0, 0.0, 2.5), gamma=0.0, seed=6)
    assert strong(ms.search(cube, frames, grid.wcs, grid.scale_arcsec)) == []


def test_a_motion_no_orbit_around_the_sun_allows_is_not_reported():
    # bright, on a dead straight path at 1 arcsec per hour: straight means far away, and nothing far away that
    # belongs to the Sun moves that fast (a satellite's ghost, a leftover that drifts with the pointing)
    cube, frames, grid = field(mover=(30.0, 52.0, 1.0 / SCALE, 0.0, 6.0), gamma=0.0, seed=3)
    out = ms.search(cube, frames, grid.wcs, grid.scale_arcsec)
    assert strong(out) == [] and out["stats"]["rejected"].get("unbound", 0) >= 1


def test_the_bend_of_the_path_gives_the_distance():
    # 6 sigma per frame, 40 au: the path leaves its straight line by about four pixels
    cube, frames, grid = field(mover=(48.0, 48.0, 0.04, 0.02, 6.0), gamma=1 / 40.0, seed=8)
    out = ms.search(cube, frames, grid.wcs, grid.scale_arcsec)
    assert len(strong(out)) == 1
    d = out["tracks"][0].extra["stack_distance"]
    assert d["from"] == "centroids"
    assert d["distance_min_au"] <= 40.0 <= d["distance_max_au"] and d["distance_au"] == pytest.approx(40.0, rel=0.2)


def test_a_track_of_the_linker_is_not_reported_again(faint):
    mover, cube, frames, grid, out = faint
    again = ms.search(cube, frames, grid.wcs, grid.scale_arcsec, exclude=out["tracks"])
    assert strong(again) == [] and again["stats"]["rejected"].get("known", 0) >= 1
