"""The measurement pipeline on synthetic data (no network)."""

import math

import numpy as np
import pytest
from astropy.wcs import WCS

from sphereblink import measure as M
from sphereblink import measurements, refcat
from sphereblink.sia import Exposure

RNG = np.random.default_rng(7)


def exposure(mjd, det=3, obs=None):
    return Exposure(obs_id=obs or f"X{mjd:.4f}", detector=det, collection="t", mjd_start=mjd, mjd_end=mjd + 0.001,
                    wave_min_um=1, wave_max_um=2, s3_key="k", access_url="u", s_region="", ra_center=0, dec_center=0)


def star_image(shape, stars, noise=0.02, width=0.6, rng=RNG):
    """Pixel-integrated Gaussian stars [(x, y, flux)] on a noisy sky."""
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]].astype(float)
    img = rng.normal(0.0, noise, shape)
    for x, y, f in stars:
        img += f * M._gauss_pix(xx, yy, x, y, width)
    return img


def stamp_of(body, mjd, det=3, noise=0.02, flux=40.0, angle=0.0, extra=(), rng=RNG, shape=(49, 81)):
    """A stamp whose WCS puts ``body`` (at its position on ``mjd``) at a random sub-pixel place near the centre."""
    w = WCS(naxis=2)
    ra, dec = body.at(mjd)
    cx, cy = shape[1] // 2 + rng.uniform(-0.5, 0.5), shape[0] // 2 + rng.uniform(-0.5, 0.5)
    s = M.PIX / 3600.0
    w.wcs.crpix = [cx + 1, cy + 1]
    w.wcs.crval = [ra, dec]
    c, sn = math.cos(angle), math.sin(angle)
    w.wcs.cd = [[-s * c, s * sn], [s * sn, s * c]]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    stars = [(cx, cy, flux)]
    for b, f in extra:
        x, y = w.world_to_pixel_values(*b.at(mjd))
        stars.append((float(x), float(y), f))
    data = star_image(shape, stars, noise, rng=rng)
    return M.make_stamp(exposure(mjd, det), w, 0, 0, data, np.zeros(shape, dtype=np.int32), {})


def test_parallax_factors_follow_the_earth():
    # a star at the ecliptic pole is displaced by one parallax all year round
    pe, pn = M.parallax_factors(270.0, 66.56, np.linspace(60000, 60365, 25))
    assert np.hypot(pe, pn) == pytest.approx(1.0, abs=0.03)
    b = M.Body("x", 10.0, 20.0, 2016.0, 1000.0, -500.0)
    ra, dec = b.at(M.MJD_J2000 + 365.25 * 26)  # ten years later
    assert M.offset(ra, dec, 10.0, 20.0) == pytest.approx((10.0, -5.0), abs=1e-3)


def test_psf_fit_recovers_subpixel_position_and_flux():
    errs, fluxes = [], []
    for _ in range(40):
        x, y = 20 + RNG.uniform(-0.5, 0.5), 15 + RNG.uniform(-0.5, 0.5)
        img = star_image((31, 41), [(x, y, 30.0)], noise=0.02, width=0.45)
        f = M.fit_psf(img, x + RNG.uniform(-0.7, 0.7), y + RNG.uniform(-0.7, 0.7), 0.02)
        assert f is not None and f.ok and f.snr > 50
        errs.append(math.hypot(f.x - x, f.y - y))
        fluxes.append(f.flux)
    assert np.median(errs) < 0.02  # pixels: 0.1 arcsec
    assert np.median(fluxes) == pytest.approx(30.0, rel=0.02)


def test_psf_fit_is_not_pulled_by_a_known_neighbour():
    x, y = 20.2, 15.4
    img = star_image((31, 41), [(x, y, 5.0), (x + 1.6, y + 0.3, 40.0)], noise=0.02, width=0.6)
    alone = M.fit_psf(img, x, y, 0.02)
    with_nb = M.fit_psf(img, x, y, 0.02, neighbours=[(x + 1.6, y + 0.3)])
    assert math.hypot(alone.x - x, alone.y - y) > 0.5  # latched on to the bright star
    assert math.hypot(with_nb.x - x, with_nb.y - y) < 0.1
    assert with_nb.flux == pytest.approx(5.0, rel=0.15)


def test_masked_core_falls_back_to_the_wings():
    yy, xx = np.mgrid[0:49, 0:81].astype(float)
    x, y = 40.3, 24.6
    r2 = (xx - x) ** 2 + (yy - y) ** 2
    img = 5000.0 * (1 + r2 / 1.5**2) ** -1.8 + RNG.normal(0, 0.05, xx.shape)
    img[r2 < 5.0**2] = np.nan  # saturated core
    assert M.fit_psf(img, x, y, 0.05) is None
    f = M.fit_wings(img, x + 0.4, y - 0.3, 0.05)
    assert f is not None and f.ok and f.kind == "wings"
    assert math.hypot(f.x - x, f.y - y) < 0.1
    assert 4.5 < f.width < 6.5  # the masked radius
    flux, err = M.ring_flux(img, f.x, f.y, 0.05, 6.5, 9.5)
    flux2, _ = M.ring_flux(2 * img, f.x, f.y, 0.05, 6.5, 9.5)
    assert flux2 == pytest.approx(2 * flux, rel=1e-6) and err > 0


def test_track_of_a_fast_mover_is_recovered_with_parallax():
    truth = M.Body("mover", 150.0, -20.0, 2016.0, -3000.0, 5000.0, 400.0)
    catalogue = M.Body("mover", 150.0, -20.0, 2016.0, -2800.0, 5100.0, 400.0)  # the prediction is a little off
    stamps = []
    for start in (60800.0, 60985.0, 61165.0, 61350.0):
        for k in range(12):
            mjd = start + k * 0.9
            st = stamp_of(truth, mjd, det=1 + k % 3, angle=0.4 if start in (60800.0, 61165.0) else 3.5)
            st.exposure.obs_id = f"o{mjd:.3f}"
            stamps.append(st)
    out = M.measure_all(stamps, catalogue, [], {})
    tr = out["track"]
    assert out["stats"]["single"] == 48 and tr["n_passes"] == 4 and tr["pm_fitted"]
    assert tr["pmra"] == pytest.approx(-3000.0, abs=4 * tr["pmra_err"] + 30)
    assert tr["pmdec"] == pytest.approx(5000.0, abs=4 * tr["pmdec_err"] + 30)
    assert tr["pmra_err"] < 150 and tr["rms_arcsec"] < 0.3
    assert tr["catalog_dpmra"] == pytest.approx(-200.0, abs=150)
    # left free, the parallax comes out too
    assert tr["parallax_fit_mas"] == pytest.approx(400.0, abs=3 * tr["parallax_fit_err"])
    # and the fitted track reproduces a measured position
    d = out["detections"][0]
    dt = M.year_of(d["mjd"]) - tr["epoch"]
    ra, dec = M.shift(tr["ra"], tr["dec"], tr["pmra"] / 1000 * dt, tr["pmdec"] / 1000 * dt)
    assert math.hypot(*M.offset(d["ra"], d["dec"], ra, dec)) < 0.6  # parallax (0.4 arcsec) is not in the straight line


def test_ignoring_parallax_would_bias_the_motion():
    # two passes half a year apart see the star from opposite sides of the orbit
    b = M.Body("near", 150.0, 5.0, 2016.0, 0.0, 0.0, 500.0)
    e1, n1 = b.offset_at(60985.0)
    e2, n2 = b.offset_at(61165.0)
    assert float(np.hypot(e2 - e1, n2 - n1)[0]) > 0.8  # arcsec, with zero proper motion


def test_faint_mover_is_measured_on_stacks():
    rng = np.random.default_rng(3)
    target = M.Body("faint", 133.76, -7.24, 2016.0, -8000.0, 700.0, 440.0)
    stamps = []
    for start in (60800.0, 60985.0, 61165.0):
        for k in range(24):
            mjd = start + k * 0.5
            # about 2 sigma per exposure: invisible one at a time
            st = stamp_of(target, mjd, det=6, noise=0.12, flux=1.2, angle=rng.uniform(0, 6.28), rng=rng)
            st.exposure.obs_id = f"o{mjd:.3f}"
            stamps.append(st)
    out = M.measure_all(stamps, target, [], {})
    assert out["stats"]["single"] == 0 and out["stats"]["stacks"] == 3
    for d in out["detections"]:
        assert d["method"] == "stack" and d["n_exposures"] >= 20 and d["snr"] >= M.STACK_MIN_SNR
        assert math.hypot(d["east"], d["north"]) < 4 * d["pos_err_arcsec"] + 0.5
        assert d["flux_ujy"] == pytest.approx(1.2 * M.TO_UJY, rel=0.4)
    assert out["track"]["pm_fitted"] and out["track"]["pmra"] == pytest.approx(-8000.0, abs=3 * out["track"]["pmra_err"] + 100)


def test_light_curve_divides_out_the_spectrum():
    dets = []
    for p, (start, level) in enumerate(((60800.0, 1.0), (60985.0, 1.5), (61165.0, 0.8))):
        for k in range(40):
            wave = 0.8 + 4.0 * RNG.uniform()
            spectrum = 1000.0 * wave**2  # a steep spectrum: fluxes differ by 30x across the band
            flux = spectrum * level * (1 + RNG.normal(0, 0.03))
            dets.append(M.Detection(f"o{p}{k}", 1 + k % 6, "t", start + k * 0.3, 10.0, 10.0, 0.2, 0.0, 0.0, wave, flux, flux * 0.03, 30.0))
    lc = M.light_curve(dets)
    levels = [r["rel_flux"] for r in lc["passes"]]
    assert levels[1] / levels[2] == pytest.approx(1.5 / 0.8, rel=0.08)
    assert lc["amplitude"] == pytest.approx(1.5 / 0.8, rel=0.08) and lc["variable"]
    flat = [M.Detection(f"f{k}", 1, "t", 60800.0 + 180 * (k % 3) + k * 0.01, 10.0, 10.0, 0.2, 0.0, 0.0, 0.8 + 0.03 * k, 500.0 * (1 + RNG.normal(0, 0.03)), 15.0, 30.0)
            for k in range(120)]
    assert not M.light_curve(flat)["variable"]


def test_split_passes_and_neighbours():
    assert M.split_passes([1.0, 2.0, 200.0, 3.0, 201.0, 400.0]).tolist() == [0, 0, 1, 0, 1, 2]
    target = M.Body("t", 10.0, 10.0, 2016.0, 5000.0, 0.0, 0.0, catalog_id="42")
    field = [M.Body("self", 10.0, 10.0, 2016.0, 5000.0, 0.0, 0.0, catalog_id="42"),
             M.Body("close", 10.0 + 60.0 / 3600, 10.0, 2016.0), M.Body("far", 10.2, 10.0, 2016.0)]
    # ten years on the target has moved 50 arcsec east, next to "close"
    assert [b.name for b in M.near_path(target, field, M.MJD_J2000 + 365.25 * 26)] == ["close"]


def test_challenge_targets_have_catalogue_astrometry():
    from sphereblink.resolve import FEATURED, resolve

    assert set(refcat.TARGETS) == set(FEATURED)
    for key in FEATURED:
        body, kind = refcat.body_for(resolve(key))
        assert body is refcat.TARGETS[key]["body"] and kind in ("star", "brown_dwarf", "variable")
        ra, dec = body.at(61000.0)
        assert 0 <= ra < 360 and abs(dec) < 90
    # anything else: the Gaia star at the position, or the bare position
    t = resolve("10.0 20.0")
    star = M.Body("Gaia DR3 1", 10.0003, 20.0, 2016.0, 0.0, 0.0, 0.0, 12.0, "Gaia DR3", "1")
    assert refcat.body_for(t, [star])[0].catalog_id == "1"
    body, kind = refcat.body_for(t, [])
    assert kind == "candidate" and body.pmra == 0.0


def test_results_are_stored_and_served_like_the_team_database(tmp_path, monkeypatch):
    from sphereblink import config

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_URL", "")
    assert not measurements.enabled() and measurements.near(150.0, -20.0, 0.1) == []
    truth = M.Body("mover", 150.0, -20.0, 2016.0, -3000.0, 5000.0, 400.0)
    stamps = [stamp_of(truth, s + k * 0.9, det=1 + k % 2) for s in (60800.0, 60985.0) for k in range(8)]
    for i, st in enumerate(stamps):
        st.exposure.obs_id = f"o{i}"
    result = M.measure_all(stamps, truth, [], {})
    ra, dec = truth.at(61000.0)
    result.update(name="mover", kind="star", ra=ra, dec=dec, note="", version=1, measured_at=0.0)
    measurements.save_local("mover", result)
    assert measurements.enabled() and measurements.load_local("mover")["name"] == "mover"
    assert measurements.list_local()[0]["key"] == "mover" and "detections" not in measurements.list_local()[0]
    assert measurements.near(10.0, 10.0, 0.1) == []
    (src,) = measurements.near(ra, dec, 0.1)
    assert src["name"] == "mover" and len(src["detections"]) == 16 and src["track"]["n_detections"] == 16
    assert {"obs_id", "detector", "mjd", "ra", "dec", "used"} <= set(src["detections"][0])
    assert not measurements.save_db(result)  # no database configured


def test_results_measured_without_a_database_are_written_once_it_is_there(tmp_path, monkeypatch):
    from sphereblink import config

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_URL", "")
    for name, stored in (("a", False), ("b", True), ("c", False)):
        measurements.save_local(name, {"name": name, "ra": 1.0, "dec": 2.0, "track": {"n_detections": 3}, "stats": {"in_database": stored}})
    measurements.save_local("nothing", {"name": "nothing", "ra": 1.0, "dec": 2.0, "track": None, "stats": {}})  # never written
    assert measurements.sync_db() == 0  # no database: nothing to do
    written = []
    monkeypatch.setattr(config, "DB_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(measurements, "save_db", lambda result: written.append(result["name"]) or True)
    assert measurements.sync_db() == 2 and sorted(written) == ["a", "c"]
    assert all(r["stats"]["in_database"] for r in measurements.list_local() if r["track"])
    assert measurements.sync_db() == 0 and len(written) == 2
    monkeypatch.setattr(measurements, "save_db", lambda result: False)  # database away: keep the result for next time
    measurements.save_local("d", {"name": "d", "ra": 1.0, "dec": 2.0, "track": {"n_detections": 3}, "stats": {}})
    assert measurements.sync_db() == 0 and not measurements.load_local("d")["stats"].get("in_database")


def saturated_stamp(body, mjd, flux, n_sat):
    """A bright star with ``n_sat`` overflowed pixels at its centre: they are flagged and read too low."""
    st = stamp_of(body, mjd, flux=flux)
    x, y = st.pixel(*body.at(mjd))
    cx, cy = int(round(x)), int(round(y))
    flags = np.zeros(st.data.shape, dtype=np.int32)
    flags[cy, cx : cx + n_sat] = 1
    data = st.data.copy()
    data[flags > 0] *= 0.3
    return M.make_stamp(st.exposure, st.wcs, 0, 0, data, flags, {"OVERFLOW": 1})


def test_a_partly_saturated_core_gives_a_position_but_no_core_flux():
    b = M.Body("bright", 80.0, 30.0)
    d = M.measure_stamp(saturated_stamp(b, 60900.0, 4000.0, 1), b)
    assert d is not None and d.method == "psf" and "SATURATED" in d.flags and not np.isfinite(d.flux_ujy)
    assert math.hypot(d.east, d.north) < 1.5


def test_a_mostly_saturated_star_gets_its_light_curve_from_the_ring():
    b = M.Body("bright", 80.0, 30.0)
    stamps = []
    for k, (start, level) in enumerate(((60800.0, 1.0), (60985.0, 1.5))):
        for i in range(40):
            st = saturated_stamp(b, start + i * 0.2, 4000.0 * level, 0 if i % 4 == 0 else 1)
            st.exposure.obs_id = f"s{k}_{i}"
            stamps.append(st)
    waves = {"t_D3": None}
    result = M.measure_all(stamps, b, [], waves)
    dets = result["detections"]
    assert len(dets) == 80 and all("RING_FLUX" in d["flags"] for d in dets)


def test_the_analytic_gradient_of_the_psf_matches_finite_differences():
    yy, xx = np.mgrid[0:7, 0:7].astype(float)
    for x0, y0, w in ((3.2, 2.9, 0.4), (2.6, 3.4, 0.8), (3.0, 3.0, 2.0)):
        g, gx, gy, gw = M._gauss_pix_grad(xx, yy, x0, y0, w)
        h = 1e-6
        assert g == pytest.approx(M._gauss_pix(xx, yy, x0, y0, w), abs=1e-14)
        assert gx == pytest.approx((M._gauss_pix(xx, yy, x0 + h, y0, w) - M._gauss_pix(xx, yy, x0 - h, y0, w)) / (2 * h), abs=1e-8)
        assert gy == pytest.approx((M._gauss_pix(xx, yy, x0, y0 + h, w) - M._gauss_pix(xx, yy, x0, y0 - h, w)) / (2 * h), abs=1e-8)
        assert gw == pytest.approx((M._gauss_pix(xx, yy, x0, y0, w + h) - M._gauss_pix(xx, yy, x0, y0, w - h)) / (2 * h), abs=1e-8)


def test_a_source_next_to_the_requested_place_is_not_the_object():
    asked = M.Body("150.00000 +20.00000", 150.0, 20.0)
    for off, found in ((1.0, True), (5.0, False)):
        there = M.Body("there", *M.shift(150.0, 20.0, off, 0.0))
        stamps = []
        for k, start in enumerate((60800.0, 60985.0, 61170.0)):
            for i in range(8):
                st = stamp_of(asked, start + i * 0.5, extra=[(there, 40.0)], flux=0.0)
                st.exposure.obs_id = f"n{k}_{i}"
                stamps.append(st)
        r = M.measure_all(stamps, asked, [], {})
        assert r["stats"]["detected"] is found and (r["track"] is not None) is found
        if not found:
            assert r["light_curve"] is None and not any(d["used"] for d in r["detections"])
            assert r["stats"]["nearby_source"]["sep_arcsec"] == pytest.approx(5.0, abs=0.5)


def test_a_search_that_found_nothing_is_not_served_as_a_source(tmp_path, monkeypatch):
    from sphereblink import config

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_URL", "")
    target = {"ra": 10.0, "dec": 5.0, "epoch": 2016.0, "pmra": 0.0, "pmdec": 0.0}
    measurements.save_local("pos", {"name": "10.0 5.0", "ra": 10.0, "dec": 5.0, "target": target, "track": None, "detections": [], "stats": {}})
    assert measurements.load_local("pos") is not None and measurements.near(10.0, 5.0, 0.1) == []
    assert not measurements.save_db(measurements.load_local("pos"))
    measurements.delete_local("pos")
    assert measurements.load_local("pos") is None


def test_stored_results_of_an_older_pipeline_are_measured_again_or_dropped(tmp_path, monkeypatch):
    from sphereblink import config, measure_api
    from sphereblink.resolve import ResolveError, ResolverUnavailable, Target

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_URL", "")
    old, new = measure_api.VERSION - 1, measure_api.VERSION
    now = __import__("time").time()

    def stored(name, version, track=True, **more):
        return {"name": name, "ra": 1.0, "dec": 2.0, "version": version, "measured_at": now, "track": {"n_detections": 3} if track else None, **more}

    measurements.save_local("barnard", stored("Barnard's Star", old))  # a challenge target: not this function's business
    measurements.save_local("fresh", stored("fresh", new))
    measurements.save_local("refused", stored("500.0 3.0", old))
    measurements.save_local("star", stored("Ross 128", old))
    measurements.save_local("empty", stored("12.34560 -33.21000", old))
    measurements.save_local("later", stored("Wolf 359", old))
    measurements.save_local("kept", stored("kept", old, query=Target("kept", 3.0, 4.0, 3.0, 4.0).to_dict()))

    def fake_resolve(text):
        if text == "500.0 3.0":
            raise ResolveError("coordinates out of range")
        if text == "Wolf 359":
            raise ResolverUnavailable("the name services are not answering")
        return Target(text, 1.0, 2.0, 1.0, 2.0)

    asked = []

    def fake_start(target, refresh=False):
        asked.append(target.name)
        key = {"Ross 128": "star_new", "12.34560 -33.21000": "empty", "kept": "kept"}[target.name]
        measurements.save_local(key, stored(target.name, new, track=target.name != "12.34560 -33.21000"))
        return key, {"status": "ready"}

    monkeypatch.setattr(measure_api, "resolve", fake_resolve)
    monkeypatch.setattr(measure_api, "start", fake_start)
    measure_api._refresh_stored()
    assert sorted(asked) == ["12.34560 -33.21000", "Ross 128", "kept"]
    left = {r["key"]: r["version"] for r in measurements.list_local()}
    assert left == {"barnard": old, "fresh": new, "star_new": new, "later": old, "kept": new}
