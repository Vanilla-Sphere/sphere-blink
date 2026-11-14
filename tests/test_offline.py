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
