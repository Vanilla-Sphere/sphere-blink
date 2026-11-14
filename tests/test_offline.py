"""Tests that need no network."""

import struct

import numpy as np
import pytest
from astropy.io import fits

from sphereblink import cutout, render, resolve
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
    assert Grid.from_fov(0, 0, 500).fov_arcmin <= 30.5


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
