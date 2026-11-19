"""Tests that need no network."""

import struct

import numpy as np
import pytest
from astropy.io import fits

from sphereblink import config, cutout, render, resolve, tiles
from sphereblink.cutout import Frame, Grid
from sphereblink.sia import Exposure


def exposure(mjd, det=1, obs="X"):
    return Exposure(obs_id=f"{obs}{mjd}", detector=det, collection="t", mjd_start=mjd, mjd_end=mjd + 0.001,
                    wave_min_um=1, wave_max_um=2, s3_key="k", access_url="u", s_region="", ra_center=0, dec_center=0)


def frame(mjd, det=1, value=0.0, n=21, seed=0):
    rng = np.random.default_rng(seed + int(mjd * 10))
    img = rng.normal(0, 1, (n, n)).astype(np.float32) + value
    return Frame(exposure(mjd, det), img, wave_um=1.5, bandwidth_um=0.03, flux_ujy=10, flux_err_ujy=1)


def test_parse_coords_decimal_and_sexagesimal():
    assert resolve.parse_coords("269.4486 +4.7387") == pytest.approx((269.4486, 4.7387))
    assert resolve.parse_coords("269.4486, -4.7387") == pytest.approx((269.4486, -4.7387))
    ra, dec = resolve.parse_coords("08h55m10.831s -07d14m42.53s")
    assert ra == pytest.approx(133.795129, abs=1e-5)
    assert dec == pytest.approx(-7.245147, abs=1e-5)
    assert resolve.parse_coords("400 10") is None
    assert resolve.parse_coords("Barnard's Star") is None


def test_featured_targets_move_with_proper_motion():
    t = resolve.resolve("barnard")
    # 10.4 arcsec/yr northwards for ~26 years
    assert (t.dec - t.dec_input) * 3600 == pytest.approx(10.36254 * (resolve.SPHEREX_EPOCH - 2000), rel=1e-6)
    assert resolve.resolve("WISE 0855").name.startswith("WISE 0855")
    with pytest.raises(ValueError):
        resolve.resolve("")


def test_grid_is_odd_north_up_and_centred():
    g = Grid.from_fov(10.0, 20.0, 8)
    assert g.npix % 2 == 1
    c = (g.npix - 1) / 2
    ra, dec = g.wcs.pixel_to_world_values(c, c)
    assert (float(ra), float(dec)) == pytest.approx((10.0, 20.0))
    _, dec_up = g.wcs.pixel_to_world_values(c, c + 1)
    ra_right, _ = g.wcs.pixel_to_world_values(c + 1, c)
    assert dec_up > 20.0 and ra_right < 10.0  # north up, east left
    assert Grid.from_fov(0, 0, 500).fov_arcmin <= config.MAX_FOV_ARCMIN + 0.5


def test_header_end_and_card_filter():
    h = fits.Header()
    h["NAXIS1"] = 2040
    h["CTYPE1"] = "RA---TAN"
    h["HIERARCH L2 N_HOT"] = 3
    h["MSKN0010"] = "HOT"
    raw = h.tostring(padding=True).encode()
    assert cutout._header_end(raw, 0) == len(raw) == 2880
    assert cutout._header_end(raw[:2000], 0) is None
    kept = cutout._parse_header(raw, 0, len(raw), cutout._WCS_CARDS)
    assert list(kept.keys()) == ["NAXIS1", "CTYPE1"]
    assert cutout.flag_planes(cutout._parse_header(raw, 0, len(raw), cutout._FLAG_CARDS)) == {"HOT": 1024}


def test_aperture_flux_recovers_a_point_source():
    yy, xx = np.mgrid[0:41, 0:41]
    star = 100.0 * np.exp(-((xx - 20) ** 2 + (yy - 20) ** 2) / (2 * 0.8**2))
    data = (star + 5.0).astype(np.float32)  # constant sky must cancel
    flags = np.zeros_like(data, dtype=np.int32)
    flux, err, names = cutout.aperture_flux(data, flags, {"HOT": 1024}, 20, 20, 3.0)
    expected = star.sum() * cutout.config.NATIVE_PIXEL_ARCSEC**2 * cutout.ARCSEC2_TO_SR * 1e12
    assert flux == pytest.approx(expected, rel=0.02)
    assert names == []
    flags[20, 20] = 1024
    assert cutout.aperture_flux(data, flags, {"HOT": 1024}, 20, 20, 3.0)[2] == ["HOT"]


CORE_PLANES = {"TRANSIENT": 1, "OVERFLOW": 2, "SUR_ERROR": 4, "HOT": 1024}
TO_UJY = cutout.config.NATIVE_PIXEL_ARCSEC**2 * cutout.ARCSEC2_TO_SR * 1e12


def _core(peak: float, x: float = 20.0, y: float = 20.0, sigma: float = 0.6):
    """A star on a flat sky of 5: the archive's pixels, and its true flux in an aperture of 2 pixels."""
    yy, xx = np.mgrid[0:41, 0:41]
    star = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    star *= peak / star.max()
    return (star + 5.0).astype(np.float32), float(star[(xx - x) ** 2 + (yy - y) ** 2 <= 4].sum()) * TO_UJY


def _measure(raw, flags, x=20.0, y=20.0):
    """Aperture flux the way the engine asks for it: flagged pixels masked in one array, kept in the other."""
    data = np.where(flags != 0, np.nan, raw).astype(np.float32)
    return cutout.aperture_flux(data, flags, CORE_PLANES, x, y, 2.0, raw=raw)


def test_flags_a_bright_star_sets_on_its_own_core_do_not_hide_it():
    raw, true = _core(400.0, 20.2, 19.9)
    flags = np.zeros(raw.shape, np.int32)
    flags[20, 20] = 2  # the brightest pixel overflowed...
    flags[19:22, 19:22] |= 1 | 4  # ...and the on-board fit complains about its neighbours
    flux, err, names = _measure(raw, flags, x=20.2, y=19.9)
    assert flux == pytest.approx(true, rel=1e-3) and err >= 0
    assert names == ["OVERFLOW", "SUR_ERROR", "TRANSIENT"]  # the flags are still reported
    # one flagged pixel is enough to show what hiding it did: the rest of the aperture, scaled
    # by area as before, is far below the star
    one = np.zeros(raw.shape, np.int32)
    one[20, 20] = 2
    assert _measure(raw, one, x=20.2, y=19.9)[0] == pytest.approx(true, rel=1e-3)
    old = np.where(one != 0, np.nan, raw)
    yy, xx = np.mgrid[0:41, 0:41]
    ap = (xx - 20.2) ** 2 + (yy - 19.9) ** 2 <= 4
    assert np.nansum(old[ap] - 5.0) * ap.sum() / np.isfinite(old[ap]).sum() * TO_UJY < 0.7 * true


def test_a_saturated_core_has_no_aperture_flux():
    raw, _ = _core(900.0)
    flags = np.zeros(raw.shape, np.int32)
    flags[19:22, 19:22] = 2 | 4
    hole = raw.copy()
    hole[20, 20] = np.nan  # the archive gives a pixel that filled up no value
    flux, _, names = _measure(hole, flags)
    assert np.isnan(flux) and "OVERFLOW" in names
    # the same when the target is put a pixel and a half beside the star
    assert np.isnan(_measure(hole, flags, x=21.4, y=20.6)[0])
    # and when the overflow has spread over the core: the charge of the brightest pixel is in its neighbours too
    raw, true = _core(900.0)
    assert np.isnan(_measure(raw, flags)[0])  # nine overflow pixels
    few = np.zeros(raw.shape, np.int32)
    few[20, 19:22] = 2 | 4
    assert _measure(raw, few)[0] == pytest.approx(true, rel=1e-3)  # three
    # and when no pixel is empty but the brightest is above the level up to which pixels were checked
    flags = few
    raw, true = _core(1900.0)
    assert _measure(raw, flags)[0] == pytest.approx(true, rel=1e-3)
    raw, _ = _core(2500.0)
    assert np.isnan(_measure(raw, flags)[0])


def test_a_cosmic_ray_hit_is_not_taken_for_a_star():
    raw, true = _core(8.0)  # a faint source...
    hit = raw.copy()
    hit[20, 20] += 300.0  # ...with a hit on its central pixel: nearly all the light in one pixel
    flags = np.zeros(raw.shape, np.int32)
    flags[20, 20] = 1 | 2
    assert np.isnan(_measure(hit, flags)[0])  # the central pixel cannot be guessed from the others
    # a hit at the rim of the aperture is left out and the rest still measures the source
    hit = raw.copy()
    hit[20, 22] += 300.0
    flags[:] = 0
    flags[20, 22] = 1
    flux, _, names = _measure(hit, flags)
    assert flux == pytest.approx(true, rel=0.01) and names == ["PARTIAL", "TRANSIENT"]  # and says so
    # most flagged pixels of a faint source are not hits at all: the pixel has its value and is summed
    flags[:] = 0
    flags[20, 20] = flags[21, 20] = 1 | 4
    assert _measure(raw, flags)[0] == pytest.approx(true, rel=1e-3)
    # a flag that is not one of the on-board fit (a hot pixel) always hides its pixel
    flags[20, 20] = 1024
    assert np.isnan(_measure(raw, flags)[0])


def test_spectrum_points_use_the_halo_only_when_the_core_has_no_flux():
    from sphereblink import main
    from sphereblink.cutout import Frame

    halo = _star_with_halo(2.0)

    def frame(flux, flags):
        return Frame(exposure(60900.0, 3), halo, wave_um=2.2, bandwidth_um=0.054, flux_ujy=flux, flux_err_ujy=900.0,
                     flags_at_target=flags)

    # flags on the core, and a flux: the aperture measured it
    assert main._point_flux(frame(2.4e5, ["OVERFLOW", "SOURCE"]), 3.075)[:3] == (2.4e5, 900.0, "aperture")
    # no flux because the core is saturated: the halo
    flux, err, method, flags = main._point_flux(frame(float("nan"), ["OVERFLOW", "SOURCE"]), 3.075)
    assert method == "wings" and flux > 1e6 and err / flux >= 0.2 and flags == ["OVERFLOW", "SOURCE"]
    # no flux for another reason (a dead pixel on a faint source): no point
    assert main._point_flux(frame(float("nan"), ["NONFUNC", "SOURCE"]), 3.075) is None


def test_a_stray_halo_point_among_direct_points_is_left_out():
    from sphereblink import main

    def pt(wave, method):
        return {"wave_um": wave, "bandwidth_um": 0.05, "flux_ujy": 1.0, "method": method}

    direct = [pt(2.00 + 0.02 * k, "aperture") for k in range(10)]  # 2.00 to 2.18 um
    # 2.05 lies among direct points; 2.26 has only two of them within two bandwidths; 3.0 has none
    halo = [pt(2.05, "wings"), pt(2.26, "wings"), pt(3.0, "wings")]
    kept = main._drop_stray_halo_points(direct + halo)
    assert [p["wave_um"] for p in kept if p["method"] == "wings"] == [2.26, 3.0]
    assert len([p for p in kept if p["method"] == "aperture"]) == 10
    # a saturated star has only halo points: all of them stay
    assert main._drop_stray_halo_points(halo) == halo
    # a star at the saturation limit has both kinds mixed: both stay
    mixed = direct + [pt(2.01 + 0.04 * k, "wings") for k in range(5)]
    assert len(main._drop_stray_halo_points(mixed)) == 15


def test_background_plane_removes_gradient():
    yy, xx = np.mgrid[0:80, 0:80]
    data = (3.0 + 0.05 * xx - 0.02 * yy).astype(np.float32)
    data[40, 40] += 500
    resid = data - cutout._background_plane(data)
    resid[40, 40] = 0
    assert np.abs(resid).max() < 1e-2


def test_epoch_grouping():
    frames = [frame(60900.1, 1), frame(60900.3, 2), frame(60901.2, 1), frame(60950.0, 1)]
    assert len(render.build_epochs(frames, None, "exposure")) == 4
    assert [e.meta()["n_exposures"] for e in render.build_epochs(frames, None, "day")] == [2, 1, 1]
    assert [len(e.members) for e in render.build_epochs(frames, None, "visit")] == [3, 1]
    assert len(render.build_epochs(frames, [2], "day")) == 1


def test_difference_cancels_static_sky_and_keeps_a_mover():
    frames = []
    for k in range(5):
        f = frame(60900 + k, seed=1)
        f.image[:] = 0
        f.image[::2, ::3] = 1.0          # static pattern (also sets the noise scale)
        f.image[10, 3 + 3 * k] += 50.0   # a source that moves 3 px per day
        frames.append(f)
    diff = render.difference(render.build_epochs(frames, None, "day"))
    for k, e in enumerate(diff):
        assert np.unravel_index(np.nanargmax(e.image), e.image.shape) == (10, 3 + 3 * k)
        rest = e.image.copy()
        rest[10, :] = 0
        assert np.abs(rest).max() < 1e-4


def test_cube_roundtrip():
    eps = render.build_epochs([frame(60900.1, value=3), frame(60901.1, value=-2)], None, "day")
    eps[0].image[0, 0] = np.nan
    blob, enc = render.encode_cube(eps)
    q = np.frombuffer(blob, dtype="<u2").reshape(2, 21, 21)
    assert q[0, 0, 0] == 0
    back = np.sinh(enc["a_lo"] + (q.astype(float) - 1) / 65534 * (enc["a_hi"] - enc["a_lo"]))
    ref = np.stack([e.image for e in eps])
    ok = np.isfinite(ref)
    assert np.allclose(back[ok], ref[ok], rtol=1e-3, atol=1e-3)


def test_png_and_gif_render():
    eps = render.build_epochs([frame(60900.1), frame(60901.1)], None, "day")
    ims = [render.frame_image(e, render.shared_limits(eps), size=128, title="t") for e in eps]
    assert render.png_bytes(ims[0])[:8] == b"\x89PNG\r\n\x1a\n"
    assert render.gif_bytes(ims)[:6] == b"GIF89a"
    assert struct.calcsize("<I") == 4


def test_color_epochs_borrow_missing_channels_and_say_so():
    # day 1 has red (D6) and green (D3, the fallback); blue (D2) only exists on day 2
    frames = [frame(60900.1, 6, seed=1), frame(60900.2, 3, seed=2), frame(60901.1, 2, seed=3), frame(60901.2, 6, seed=4)]
    eps = render.build_color_epochs(frames, "day")
    assert [e.image.shape for e in eps] == [(3, 21, 21)] * 2
    first = {c["color"]: c for c in eps[0].channels}
    assert (first["R"]["detector"], first["R"]["borrowed"]) == (6, False)
    assert (first["G"]["detector"], first["G"]["borrowed"]) == (3, False)
    assert (first["B"]["detector"], first["B"]["borrowed"]) == (2, True)
    assert first["B"]["date"] != first["R"]["date"]
    second = {c["color"]: c for c in eps[1].channels}
    assert (second["G"]["detector"], second["G"]["borrowed"]) == (3, True)
    assert eps[0].meta()["channels"][2]["borrowed"] is True

    blob, enc = render.encode_cube(eps)
    assert enc["planes"] == 3 and len(blob) == 2 * 3 * 21 * 21 * 2
    ims = [render.frame_image(e, render.shared_limits(eps), size=128) for e in eps]
    assert ims[0].mode == "RGB" and render.gif_bytes(ims)[:6] == b"GIF89a"
    diff = render.difference(eps)
    assert diff[0].channels == eps[0].channels and diff[0].image.shape == (3, 21, 21)


def test_color_keeps_a_grey_star_grey_whichever_detector_feeds_a_channel():
    def star(mjd, det, noise, seed):
        f = frame(mjd, det, seed=seed)
        rng = np.random.default_rng(seed)
        f.image[:] = rng.normal(0, noise, f.image.shape)
        f.image[10, 10] += 400 * noise  # same star, detectors with very different noise
        return f

    frames = [star(60900.1, 6, 1.0, 1), star(60900.2, 4, 5.0, 2), star(60900.3, 2, 0.2, 3)]
    e = render.build_color_epochs(frames, "day")[0]
    rgb = render.to_rgb8(e.image, render.shared_limits([e]))
    r, g, b = (int(v) for v in rgb[::-1][10, 10])
    assert max(r, g, b) - min(r, g, b) <= 3 and min(r, g, b) > 200


def test_disk_cache_drops_oldest_views_first(tmp_path):
    from sphereblink.cutout import Grid
    from sphereblink.service import Store

    store = Store(tmp_path / "c.sqlite")
    grids = [Grid.from_fov(10.0 + k, 5.0, 1.0) for k in range(3)]
    for k, g in enumerate(grids):
        fr = [frame(60900 + i, seed=k) for i in range(4)]
        store.put_frames(g, [(f"{k}-{i}", f.exposure, f) for i, f in enumerate(fr)])
        store._conn().execute("UPDATE frames SET created=? WHERE grid=?", (1000.0 + k, g.key()))
        store._conn().commit()
    one_view = store._conn().execute("SELECT SUM(LENGTH(image)) FROM frames WHERE grid=?", (grids[0].key(),)).fetchone()[0]
    freed = store.trim(2.5 * one_view, keep=grids[2].key())
    left = {r[0] for r in store._conn().execute("SELECT DISTINCT grid FROM frames")}
    assert freed == one_view and left == {grids[1].key(), grids[2].key()}
    store.trim(0, keep=grids[2].key())  # the view being served is never dropped
    assert {r[0] for r in store._conn().execute("SELECT DISTINCT grid FROM frames")} == {grids[2].key()}


def test_memory_budget_forgets_least_recently_used_views(tmp_path, monkeypatch):
    from sphereblink import config
    from sphereblink.service import Job, Service, Store

    svc = Service(Store(tmp_path / "c.sqlite"))
    jobs = []
    for k in range(3):
        j = Job(id=f"j{k}", status="ready", last_used=100.0 + k)
        j.frames = [frame(60900 + i, n=101) for i in range(5)]  # 5 * 101 * 101 * 4 bytes = 0.2 MB
        svc.jobs[j.id] = j
        svc.by_view[f"view{k}"] = j.id
        jobs.append(j)
    monkeypatch.setattr(config, "RAM_BUDGET_MB", 0.45)
    svc._forget(keep=jobs[2])
    assert set(svc.jobs) == {"j1", "j2"} and set(svc.by_view) == {"view1", "view2"}


def test_wide_views_hold_their_frames_in_a_file_on_disk(tmp_path, monkeypatch):
    from sphereblink import config
    from sphereblink.service import Job, Service, Spill, Store

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    spill = Spill.open(3, 101)
    job = Job(id="w", status="ready", spill=spill)
    fr = [frame(60900 + i, n=101) for i in range(4)]
    want = [f.image.copy() for f in fr]
    for f in fr:
        job.keep(f)
    # three go to the file (it was opened for three), the fourth stays in memory; all read back the same
    assert spill.used == 3 and all(np.array_equal(f.image, w) for f, w in zip(job.frames, want))
    assert all(type(f.image) is np.ndarray and f.image.dtype == np.float32 for f in job.frames)
    assert job.held() == (101 * 101 * 4, 3 * 101 * 101 * 4)
    assert list((tmp_path / "spill").iterdir()) == []  # the file has no name: nothing is left behind
    # the memory budget counts only what is in memory; files on disk have a budget of their own
    svc = Service(Store(tmp_path / "c.sqlite"))
    svc.jobs = {"w": job, "x": Job(id="x", status="ready", last_used=0.0, spill=Spill.open(2, 101))}
    svc.by_view = {"vw": "w", "vx": "x"}
    monkeypatch.setattr(config, "RAM_BUDGET_MB", 0.05)
    monkeypatch.setattr(config, "SPILL_BUDGET_GB", 1.0)
    svc._forget(keep=job)
    assert set(svc.jobs) == {"w", "x"}
    monkeypatch.setattr(config, "SPILL_BUDGET_GB", 0.0002)  # 200 kB: less than both files
    svc._forget(keep=job)
    assert set(svc.jobs) == {"w"} and svc.by_view == {"vw": "w"}


def test_wide_views_cut_fewer_exposures_at_once(monkeypatch):
    from sphereblink import config, worker

    monkeypatch.setattr(config, "FETCH_WORKERS", 64)
    monkeypatch.setattr(config, "CUT_MB", 250)
    small, wide = Grid.from_fov(10.0, 20.0, 8.0), Grid.from_fov(10.0, 20.0, 50.4)
    assert worker.cut_mb(wide) > 5 * worker.cut_mb(small)
    assert worker.slots(small) == 64 and worker.slots(small, 2) == 32  # an 8 arcmin view as before
    assert 4 <= worker.slots(wide) < 20 and worker.slots(wide) * worker.cut_mb(wide) <= 250
    assert worker.slots(wide, 8) == 4  # never fewer than four
    monkeypatch.setattr(config, "CUT_MB", 0)
    assert worker.slots(wide) == 64  # 0: no limit


# ----------------------------------------------------------------------------- progressive fields
def test_cells_follow_the_fixed_grid_and_grow_ring_by_ring():
    # ids and centres of the team's icrs012-v1 grid (Barnard's Star, Fairall 9)
    assert tiles.tile_id(*tiles.cell_of(269.452, 4.693)) == "icrs012-v1-2245-0789"
    assert tiles.tile_id(*tiles.cell_of(20.94, -58.81)) == "icrs012-v1-0174-0259"
    assert tiles.parse_tile_id("icrs012-v1-2245-0789") == (2245, 789)
    with pytest.raises(ValueError):
        tiles.parse_tile_id("icrs012-v1-9999-0789")
    block = tiles.block(269.452, 4.693, 3)
    assert len(block) == 49 and len({t["tile_id"] for t in block}) == 49
    assert [sum(1 for t in block if t["level"] == lv) for lv in range(4)] == [1, 8, 16, 24]
    assert [t["level"] for t in block] == sorted(t["level"] for t in block)  # centre first
    seed = block[0]
    assert seed["ra_c"] == pytest.approx(2245.5 * 0.12) and seed["dec_c"] == pytest.approx(789.5 * 0.12 - 90)
    assert seed["ra_min"] <= 269.452 < seed["ra_max"] and seed["dec_min"] <= 4.693 < seed["dec_max"]
    # the grid wraps in RA and stops at the pole
    assert {t["tile_id"].split("-")[2] for t in tiles.block(0.01, 0.0, 1)} == {"2999", "0000", "0001"}
    assert len(tiles.block(10.0, 89.99, 1)) == 6


def test_stages_cover_the_block_and_share_one_centre():
    assert tiles.stage_levels(3) == [0, 1, 3] and tiles.stage_levels(1) == [0, 1] and tiles.stage_levels(0) == [0]
    g0, g3 = tiles.stage_grid(269.46, 4.74, 0), tiles.stage_grid(269.46, 4.74, 3)
    assert (g0.ra, g0.dec) == (g3.ra, g3.dec)
    assert g0.fov_arcmin == pytest.approx(7.2, abs=0.1) and g3.fov_arcmin == pytest.approx(50.4, abs=0.2)
    assert len(g0.search_points()) == 1  # one cell: exposures that contain the centre
    pts = g3.search_points()  # a mosaic: a lattice that reaches the corners
    assert len(pts) == 25
    assert max(abs(d - g3.dec) for _, d in pts) == pytest.approx(0.42, abs=0.01)


def test_wide_grids_map_pixels_to_the_detector_as_exactly_as_small_ones():
    from astropy.wcs import WCS

    hdr = fits.Header()
    hdr.update(NAXIS=2, NAXIS1=2040, NAXIS2=2040, CTYPE1="RA---TAN-SIP", CTYPE2="DEC--TAN-SIP", CRPIX1=1020.5,
               CRPIX2=1020.5, CRVAL1=150.3, CRVAL2=2.1, CD1_1=-1.2e-3, CD1_2=1.2e-3, CD2_1=1.2e-3, CD2_2=1.2e-3,
               A_ORDER=3, B_ORDER=3, A_2_0=2e-6, A_0_2=-1e-6, A_1_1=1.5e-6, A_3_0=3e-10, B_2_0=-2e-6, B_0_2=1e-6,
               B_1_1=-1e-6, B_0_3=-2e-10)
    wcs = WCS(hdr)
    g = Grid.from_fov(150.4, 2.2, 50.4)
    assert g.npix > cutout.EXACT_MAP_NPIX
    px, py = cutout.detector_coords(g, wcs)
    yy, xx = np.mgrid[0 : g.npix, 0 : g.npix]
    ex, ey = wcs.world_to_pixel_values(*g.wcs.pixel_to_world_values(xx, yy))
    assert np.abs(px - ex).max() < 1e-3 and np.abs(py - ey).max() < 1e-3


def test_binned_epochs_average_only_real_pixels():
    img = np.arange(36, dtype=np.float32).reshape(6, 6)
    img[0, 0] = np.nan
    e = render.Epoch("2026-01-01", 61000.0, np.stack([img, img, img]), [], [])
    (b,) = render.binned([e], 2)
    assert b.image.shape == (3, 3, 3)
    assert b.image[0, 0, 0] == pytest.approx((1 + 6 + 7) / 3) and b.image[0, 2, 2] == pytest.approx(31.5)
    assert render.binned([e], 1)[0] is e


def test_wide_frames_survive_half_precision_storage(tmp_path):
    from sphereblink.service import HALF_PRECISION_NPIX, Store

    store = Store(tmp_path / "c.sqlite")
    g = Grid.from_fov(10.0, 20.0, 50.4)
    assert g.npix > HALF_PRECISION_NPIX
    rng = np.random.default_rng(1)
    img = rng.normal(0, 0.02, (g.npix, g.npix)).astype(np.float32)
    img[5, 5], img[7, 7] = np.nan, 812.5
    e = exposure(61000.0)
    store.put_frames(g, [("k", e, Frame(e, img, 1.0, 0.1, 5.0, 1.0))])
    back = store.get_frame("k", e).image
    assert back.dtype == np.float32 and np.isnan(back[5, 5]) and back[7, 7] == pytest.approx(812.5)
    assert np.nanmax(np.abs(back - img)) < 0.02 * 0.01 + 0.3  # noise is kept to a small fraction of itself
    assert np.nanstd(back - img) < 0.02 / 500


def test_archive_csv_answer_gives_exposures_and_skips_other_products():
    from sphereblink import sia

    cloud = '{"aws": {"bucket_name": "nasa-irsa-spherex", "key": "qr3/level2/2025W33_2C/l2b/x_D3.fits", "region": "us-east-1"}}'
    head = "s_ra,s_dec,dataproduct_subtype,dataproduct_type,energy_bandpassname,obs_id,em_min,em_max,access_url,s_region,obs_collection,t_min,t_max,cloud_access"
    rows = [
        ["150.1", "2.2", "science", "image", "SPHEREx-D3", "2025W33_2C_0321_1", "1.64e-06", "2.42e-06",
         "https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/x_D3.fits", "POLYGON ICRS 1 2 3 4 5 6", "", "60900.5", "60900.6", cloud],
        ["150.1", "2.2", "calibration", "image", "SPHEREx-D3", "cal", "1e-06", "2e-06", "u", "r", "", "1", "2", ""],
    ]
    import csv as _csv
    import io as _io

    buf = _io.StringIO()
    buf.write(head + "\n")
    _csv.writer(buf).writerows(rows)
    found = sia.parse_sia(buf.getvalue().encode(), "text/csv;charset=UTF-8", "spherex_qr3")
    assert len(found) == 1
    e = found[0]
    assert (e.uid, e.collection, e.s3_key) == ("2025W33_2C_0321_1_D3", "spherex_qr3", "qr3/level2/2025W33_2C/l2b/x_D3.fits")
    assert e.wave_min_um == pytest.approx(1.64) and e.mjd == pytest.approx(60900.55)


def _star_with_halo(level_at_5px: float, npix: int = 157, scale: float = 3.075, core: bool = True) -> np.ndarray:
    """A star at the centre: halo falling as r^-2.5, on a noisy flat sky."""
    from sphereblink import wings

    r = np.maximum(wings._radius(npix, scale), 0.3)
    img = level_at_5px * (r / 5.0) ** -2.5 + np.random.default_rng(3).normal(0, 0.004, (npix, npix))
    if core:
        img[r < 3.5] = img.max()  # saturated core, painted at the brightest level
    return img.astype(np.float32)


def test_wing_levels_follow_the_halo_and_skip_the_saturated_core():
    from sphereblink import wings

    levels = wings.ring_levels(_star_with_halo(2.0), 3.075, saturated=True)
    assert all(np.isfinite(lv[0]) and lv[2] > 0.9 for lv in levels)
    assert levels[0][0] > levels[1][0] > levels[2][0] > 0
    twice = wings.ring_levels(_star_with_halo(4.0), 3.075, saturated=True)
    assert twice[1][0] == pytest.approx(2 * levels[1][0], rel=0.03)  # a ring level is linear in the halo
    # a core that reaches into the first ring makes the next ring take over
    big = _star_with_halo(2.0)
    big[wings._radius(157, 3.075) < 5.5] = big.max()
    assert not np.isfinite(wings.ring_levels(big, 3.075, saturated=True)[0][0])
    assert wings.measure(big, 3.075, 2.2, 0.054)["ring"] == 1


def test_wing_flux_is_calibrated_corrected_and_honest_about_its_range():
    from sphereblink import wings

    faint = wings.measure(_star_with_halo(0.05), 3.075, 2.2, 0.054)
    bright = wings.measure(_star_with_halo(5.0), 3.075, 2.2, 0.054)
    huge = wings.measure(_star_with_halo(60.0), 3.075, 2.2, 0.054)
    assert 0.1e6 < faint["flux_ujy"] < 1e6 and not faint["extrapolated"]
    # 100 times the halo is less than 100 times the flux: the excess is taken out
    assert 30 < bright["flux_ujy"] / faint["flux_ujy"] < 70
    assert bright["flux_err_ujy"] / bright["flux_ujy"] == pytest.approx(0.25, abs=0.02)
    assert huge["extrapolated"] and huge["flux_err_ujy"] / huge["flux_ujy"] >= 0.5
    # the same halo through a narrow channel (R = 130) is a slower photon rate: smaller correction
    assert wings.measure(_star_with_halo(5.0), 3.075, 4.7, 4.7 / 130)["flux_ujy"] > 0
    assert wings._excess(0.1) == wings._excess(0.5) < wings._excess(5) < wings._excess(20)
    # no halo, no flux; no wavelength, no flux
    assert wings.measure(_star_with_halo(0.0, core=False), 3.075, 2.2, 0.054, saturated=False) is None
    assert wings.measure(_star_with_halo(2.0), 3.075, float("nan")) is None


def test_out_of_range_coordinates_are_refused_without_asking_the_name_services():
    class NoNetwork:
        def get(self, *a, **k):
            raise AssertionError("coordinates must not be sent to a name service")

    for text in ("400 100", "10 -95", "500.0 3.0", "-1, 5"):
        with pytest.raises(resolve.ResolveError, match="out of range"):
            resolve.resolve(text, client=NoNetwork())
    assert resolve.resolve("359.9 -90").dec == -90


def _catwise(ra, dec, pm=(0.0, 0.0), mag=14.0, snr=30.0, flags="00"):
    return {"id": "", "ra": ra, "dec": dec, "pmra": pm[0], "pmdec": pm[1], "pm_snr": snr if any(pm) else 0.0,
            "mag": mag, "parallax": None, "epoch": 2014.6, "ab_flags": flags}


def test_catwise_motions_around_a_bright_star_are_marked_and_real_movers_kept():
    from sphereblink import catalogs

    asec = 1 / 3600.0
    # a real fast mover, listed as three pieces along its path that agree, and one that does not
    mover = [_catwise(10.0 + k * 6 * asec, 5.0, (-7900.0 - 50 * k, 500.0 + 200 * k), 15.6) for k in range(3)]
    stray = _catwise(10.0 + 9 * asec, 5.0, (-4200.0, -3600.0), 16.1)
    # two unrelated stars with honest motions in the same field
    slow = [_catwise(10.03, 5.03, (-170.0, -140.0), 13.3), _catwise(10.012, 5.001, (90.0, -260.0), 15.7, snr=8.0)]
    quiet = [_catwise(10.05 + 0.002 * k, 4.95, (5.0, -3.0), 12.0 + k, snr=2.0) for k in range(5)]
    field = mover + [stray] + slow + quiet
    out = catalogs.flag_artifacts([dict(s) for s in field])
    assert not any(s.get("suspect") for s in out)
    assert out[0]["pmra"] == -7900.0 and "ab_flags" not in out[0]

    # the same field with a bright star in it: its halo is listed as a dozen "fast" sources
    star = _catwise(10.2, 5.0, (3200.0, 100.0), 8.5, snr=100.0, flags="0H")
    halo = [_catwise(10.2 + (20 + 12 * k) * asec * (-1) ** k, 5.0 + 15 * k * asec, (300.0 * (k + 1), -200.0 * k), 13.0 + k / 4)
            for k in range(12)]
    out = catalogs.flag_artifacts([dict(s) for s in field + [star] + halo])
    n = len(field)
    assert all(s.get("suspect") and s["pmra"] == 0.0 and s["pm_snr"] == 0.0 for s in out[n:])
    assert not any(s.get("suspect") for s in out[:n])  # 12 arcmin away: untouched

    # a lone entry next to a merely bright, unsaturated star, and an impossible motion
    out = catalogs.flag_artifacts([_catwise(20.0, 0.0, mag=6.0), _catwise(20.0 + 50 * asec, 0.0, (240.0, 10.0)),
                                   _catwise(21.0, 0.0, (20000.0, 0.0))])
    assert [bool(s.get("suspect")) for s in out] == [False, True, True]


def test_exported_pictures_carry_the_nasa_credit():
    eps = render.build_epochs([frame(60900.1), frame(60901.1)], None, "day")
    limits = render.shared_limits(eps)
    with_credit = render.frame_image(eps[0], limits, size=360, title="t")
    without = render.frame_image(eps[0], limits, size=360, title="t", credit=False)
    assert np.asarray(with_credit).tobytes() != np.asarray(without).tobytes()
    assert b"NASA/JPL-Caltech" in render.gif_bytes([with_credit, with_credit])


def test_static_files_say_how_long_they_may_be_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("SPHEREX_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("SPHEREX_AUTO_MEASURE", "0")
    from fastapi.testclient import TestClient

    from sphereblink import main

    c = TestClient(main.app)
    assert "immutable" in c.get("/static/app.js?v=1").headers["cache-control"]
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert "max-age=86400" in c.get("/static/favicon.svg").headers["cache-control"]
    assert c.head("/").status_code == 200
    assert "Disallow: /api/" in c.get("/robots.txt").text
    assert c.get("/favicon.ico").headers["content-type"] == "image/png"
    assert c.get("/api/resolve", params={"q": "500 3"}).status_code == 400
    assert c.get("/api/view", params={"ra": 400, "dec": 100}).status_code == 400


def test_ipac_table_is_read_by_its_column_edges():
    from sphereblink import catalogs

    text = "\n".join([
        "\\fixlen = T",
        "\\ ra (deg)",
        "|                source_name|           ra|          dec|  w2mpro|         ab_flags|",
        "|                       char|       double|       double|  double|             char|",
        "         J085509.47-071502.9   133.7894629    -7.2508305   13.756                00 ",
        "         J085511.23-071522.6   133.7968029    -7.2562793     null                H0 ",
    ])
    rows = catalogs.parse_ipac_table(text)
    assert [r["source_name"] for r in rows] == ["J085509.47-071502.9", "J085511.23-071522.6"]
    assert rows[0]["ra"] == "133.7894629" and rows[0]["w2mpro"] == "13.756" and rows[0]["ab_flags"] == "00"
    assert rows[1]["w2mpro"] == "" and rows[1]["ab_flags"] == "H0"
    with pytest.raises(ValueError):
        catalogs.parse_ipac_table("<html>busy</html>")


def test_catalogue_falls_back_to_the_second_service(monkeypatch):
    from sphereblink import catalogs

    asked = []

    def down(*a):
        asked.append("first")
        raise TimeoutError("no answer")

    def up(*a):
        asked.append("second")
        return [{"id": "1", "ra": "10.0", "dec": "-5.0", "pmra": "3.0", "pmdec": "4.0", "epmra": "0.1", "epmdec": "0.1",
                 "mag": "12.5", "parallax": "2.0"}]

    monkeypatch.setitem(catalogs.SOURCES, "gaia", (down, up))
    out = catalogs.query("gaia", 10.0, -5.0, 0.1)
    assert asked == ["first", "second"]
    assert out == [{"id": "1", "ra": 10.0, "dec": -5.0, "pmra": 3.0, "pmdec": 4.0, "pm_snr": 50.0, "mag": 12.5,
                    "parallax": 2.0, "epoch": 2016.0}]
    monkeypatch.setitem(catalogs.SOURCES, "gaia", (down, down))
    with pytest.raises(TimeoutError):
        catalogs.query("gaia", 10.0, -5.0, 0.1)


def test_warm_up_covers_the_views_the_viewer_asks_for():
    from sphereblink import main

    keys = {g.key() for g in main.featured_grids()}
    assert len(keys) == len(resolve.FEATURED) * 7  # four plain fields and three progressive stages each
    for name in resolve.FEATURED:
        # a plain field, asked for by name
        _, grid = main._view(name, None, None, 15.0)
        assert grid.key() in keys
        # a progressive stage, asked for by the position and size /api/tiles reports
        t = resolve.resolve(name)
        ra_c, dec_c = tiles.center(*tiles.cell_of(t.ra, t.dec))
        for level in (0, 1, 3):
            g = tiles.stage_grid(ra_c, dec_c, level)
            _, grid = main._view(None, g.ra, g.dec, round((2 * level + 1) * tiles.STEP * 60.0, 3))
            assert grid.key() in keys
