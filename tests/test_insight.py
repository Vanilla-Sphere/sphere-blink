"""The preliminary reading of a spectrum (web/insight.js) and its reference wavelengths (web/linelist.js),
on real and made-up curves.

The rules run in the browser, so the tests hand the spectra to the same file under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).parent
SPECTRA = HERE / "data" / "spectra"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node to run web/insight.js")


def real(name):
    d = json.loads((SPECTRA / f"{name}.json").read_text())
    return [
        {"wave_um": w, "bandwidth_um": bw, "flux_ujy": f, "flux_err_ujy": e, "method": "wings" if wings else "aperture",
         "mjd": mjd, "flags": ["EXTRAPOLATED"] if extra else []}
        for w, bw, f, e, wings, mjd, extra in d["points"]
    ]


def planck(wave, temp):
    return 1.0 / (wave**3 * np.expm1(14387.8 / (wave * temp)))


def made_up(shape, level=20000.0, noise=0.02, n=360, seed=1, method="aperture"):
    """A spectrum sampled as SPHEREx does: n exposures shared among the six detectors, on two dates."""
    rng = np.random.default_rng(seed)
    edges = (0.75, 1.11, 1.64, 2.42, 3.82, 4.42, 5.0)   # each detector takes the same number of exposures
    wave = np.sort(np.concatenate([rng.uniform(lo, hi, n // 6) for lo, hi in zip(edges[:-1], edges[1:])]))
    n = len(wave)
    width = wave / np.where(wave < 3.82, 41.0, 120.0)
    flux = shape(wave)
    flux = flux / np.median(np.abs(flux)) * level if np.median(np.abs(flux)) > 0 else flux
    err = np.full(n, noise * level)
    flux = flux + rng.normal(0, err)
    mjd = np.where(rng.random(n) < 0.5, 60800.0, 60990.0)
    return [
        {"wave_um": round(float(w), 4), "bandwidth_um": round(float(b), 4), "flux_ujy": float(f), "flux_err_ujy": float(e),
         "method": method, "mjd": float(m), "flags": []}
        for w, b, f, e, m in zip(wave, width, flux, err, mjd)
    ]


def dip(wave, at, width, depth):
    return 1.0 - depth * np.exp(-0.5 * ((wave - at) / width) ** 2)


CASES = {
    # real curves
    **{name: None for name in (
        "epsilon-indi-b", "elias-16", "elias-29", "ngc-7027", "g191-b2b", "wise-0855", "empty-sky", "barnards-star",
        "3c-273", "ngc-3379", "vega", "r-draconis", "rw-aurigae", "sgr-a", "pg-1634-706", "apm-08279-5255", "sdss-j0100-2802")},
    # made-up curves
    "hot": lambda w: planck(w, 12000),
    "cool": lambda w: planck(w, 3000),
    "noise": lambda w: np.zeros_like(w),
    "red": lambda w: w**1.5,
    "iced": lambda w: planck(w, 1200) * dip(w, 3.05, 0.14, 0.6) * dip(w, 4.265, 0.02, 0.7),
    "lines": lambda w: planck(w, 9000) * (1 + 3 * np.exp(-0.5 * ((w - 1.8756) / 0.02) ** 2) + 3 * np.exp(-0.5 * ((w - 4.0523) / 0.015) ** 2)),
    "cool-noisy": lambda w: planck(w, 3000),
}

# published redshifts (SIMBAD) of the quasars among the real curves: each is also read with its redshift, as "<name>@z"
REDSHIFTS = {"3c-273": 0.158, "pg-1634-706": 1.337, "apm-08279-5255": 3.911, "sdss-j0100-2802": 6.327}


@pytest.fixture(scope="module")
def readings():
    spectra = []
    for name, shape in CASES.items():
        if shape is None:
            points = real(name)
        elif name == "noise":
            points = made_up(lambda w: np.ones_like(w), level=0.0)
            for p in points:
                p["flux_err_ujy"] = 60.0
            rng = np.random.default_rng(5)
            for p in points:
                p["flux_ujy"] = float(rng.normal(0, 60.0))
        else:
            points = made_up(shape, noise=0.25 if name == "cool-noisy" else 0.02)
        spectra.append({"name": name, "points": points})
    for name, z in REDSHIFTS.items():
        spectra.append({"name": f"{name}@z", "points": real(name), "opts": {"z": z, "zSource": "SIMBAD"}})
    # H alpha and H beta of a made-up source at z = 2, read without the redshift, with it and with a wrong one
    shifted = made_up(lambda w: w**0.5 * (1 + 2 * np.exp(-0.5 * ((w - 3 * 0.65646) / 0.025) ** 2) + np.exp(-0.5 * ((w - 3 * 0.48627) / 0.02) ** 2)))
    spectra.append({"name": "shifted", "points": shifted})
    spectra.append({"name": "shifted@z", "points": shifted, "opts": {"z": 2.0}})
    spectra.append({"name": "shifted@wrong", "points": shifted, "opts": {"z": 2.6}})
    spectra.append({"name": "hot@z", "points": made_up(CASES["hot"]), "opts": {"z": 1.5}})
    spectra.append({"name": "few", "points": made_up(lambda w: planck(w, 5000), n=8)})
    spectra.append({"name": "nothing", "points": []})
    run = subprocess.run(["node", str(HERE / "insight_runner.js")], input=json.dumps(spectra), capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def features(reading):
    return {f["key"] for f in reading["features"] if not f["tentative"]}


def test_every_reading_is_complete(readings):
    for name, r in readings.items():
        assert r["headline"] and r["plain"], name
        assert r["confidence"] in ("low", "moderate", "good"), name
        assert r["state"] in ("none", "ok"), name
        for f in r["findings"]:
            assert f["text"].strip() and "NaN" not in f["text"] and "undefined" not in f["text"], (name, f)
        for c in r["caveats"]:
            assert "NaN" not in c and "undefined" not in c, (name, c)


def test_brown_dwarf_shows_water_and_methane(readings):
    r = readings["epsilon-indi-b"]
    assert r["metrics"]["kind"] == "browndwarf"
    assert {"h2o_1p4", "h2o_1p9", "ch4"} <= features(r)
    # the flux peaks between the bands are not taken for emission lines
    assert not [f for f in r["features"] if f["kind"] == "emission"]
    assert r["confidence"] == "good"


@pytest.mark.parametrize("name", ["elias-16", "elias-29"])
def test_ices_behind_a_dark_cloud(readings, name):
    r = readings[name]
    assert r["metrics"]["kind"] == "ice"
    assert {"h2o_ice", "co2"} <= features(r)
    assert 0.5 < r["metrics"]["bands"]["at_3p0"] < 0.85
    assert "h2o_2p7" not in features(r) and "ch4" not in features(r)


def test_planetary_nebula_shows_hydrogen_lines(readings):
    r = readings["ngc-7027"]
    assert r["metrics"]["kind"] == "emission"
    assert {"pa_alpha", "br_alpha", "pah"} <= features(r)


def test_white_dwarf_is_a_featureless_hot_tail(readings):
    r = readings["g191-b2b"]
    assert r["metrics"]["kind"] == "hot" and not r["features"]
    assert -2.3 < r["metrics"]["slope"] < -1.6
    assert r["confidence"] == "good"
    # no temperature is claimed from the Rayleigh-Jeans tail
    assert not any(f["key"] == "blackbody" for f in r["findings"])


def test_cold_neighbour_shows_as_a_second_component(readings):
    r = readings["wise-0855"]
    assert r["metrics"]["kind"] == "two"
    assert r["confidence"] != "good"


def test_empty_sky_names_no_object(readings):
    for name in ("empty-sky", "noise"):
        r = readings[name]
        assert r["state"] == "none" and not r["features"], name
        assert "kind" not in r["metrics"], name


def test_cool_star_gets_a_temperature_range(readings):
    r = readings["barnards-star"]
    assert r["metrics"]["kind"] == "peak"
    lo, hi = r["metrics"]["blackbody_range_K"]
    assert lo < 3200 < hi           # Barnard's Star: 3200 K
    assert r["confidence"] == "moderate"   # measured from the halo


def test_quasar_reads_as_a_red_source_without_the_halo_points(readings):
    r = readings["3c-273"]
    assert r["metrics"]["kind"] == "red"
    assert r["metrics"]["points_used"] < r["metrics"]["points"]
    assert any("left out" in c for c in r["caveats"])


def test_extended_galaxy_is_not_read_from_its_halo(readings):
    r = readings["ngc-3379"]
    assert r["metrics"]["method_ratio"] > 3
    assert r["metrics"]["halo_fraction"] == 0
    assert r["metrics"]["kind"] == "partial" and r["confidence"] == "low"
    assert any("extended" in c for c in r["caveats"])


def test_saturated_sources_claim_little(readings):
    vega = readings["vega"]
    assert vega["metrics"]["kind"] == "saturated" and vega["confidence"] == "low"
    assert not any(f["key"] in ("blackbody", "peak", "slope") for f in vega["findings"])
    mira = readings["r-draconis"]
    assert mira["confidence"] == "low"
    assert mira["metrics"]["kind"] in ("coolstar", "saturated")
    assert "brown dwarf" not in mira["headline"]   # tens of jansky: not a brown dwarf
    assert readings["sgr-a"]["confidence"] == "low"


def test_young_star_with_a_disc_reads_as_red(readings):
    r = readings["rw-aurigae"]
    assert r["metrics"]["kind"] == "red"
    assert r["confidence"] != "good"


def test_made_up_curves(readings):
    assert readings["hot"]["metrics"]["kind"] == "hot" and not readings["hot"]["features"]
    cool = readings["cool"]
    assert cool["metrics"]["kind"] == "peak"
    assert cool["metrics"]["blackbody_range_K"][0] <= 3000 <= cool["metrics"]["blackbody_range_K"][1]
    assert abs(cool["metrics"]["blackbody_K"] - 3000) < 250
    assert readings["red"]["metrics"]["kind"] == "red" and not readings["red"]["features"]
    assert readings["iced"]["metrics"]["kind"] == "ice" and {"h2o_ice", "co2"} <= features(readings["iced"])
    assert readings["lines"]["metrics"]["kind"] == "emission" and {"pa_alpha", "br_alpha"} <= features(readings["lines"])


def test_noise_makes_no_features(readings):
    # the same cool star at a signal-to-noise of 4 per point: no band or line may appear
    r = readings["cool-noisy"]
    assert not features(r)
    assert r["confidence"] != "good"


def test_too_few_points(readings):
    for name in ("few", "nothing"):
        r = readings[name]
        assert r["state"] == "none" and r["confidence"] == "low" and not r["findings"]


# ---------------------------------------------------------------- the reference wavelengths

@pytest.fixture(scope="module")
def catalog():
    run = subprocess.run(["node", str(HERE / "insight_runner.js"), "catalog"], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_every_reference_wavelength_names_its_source(catalog):
    keys = [e["key"] for e in catalog["lines"] + catalog["bands"]]
    assert len(keys) == len(set(keys))
    for e in catalog["lines"] + catalog["bands"]:
        assert e["source"] in catalog["sources"], e["key"]
        assert e["label"] and e["species"] and e["transition"], e["key"]
    for e in catalog["lines"]:
        assert 0.1 < e["w"] < 5.0, e["key"]
        for part in e.get("parts", []):
            assert abs(part - e["w"]) < 0.002 * e["w"], e["key"]   # a multiplet SPHEREx cannot split
    for e in catalog["bands"]:
        assert e["also"] and all(k in catalog["sources"] for k in e["also"]) and e["where"], e["key"]
        # the wavelength is the one of the wavenumbers read in the source
        assert abs(e["w"] - np.mean([1e4 / k for k in e["cm"]])) < 0.006, e["key"]
        assert e["inb"][0] < e["at"] < e["inb"][1] and e["left"][1] <= e["inb"][0] and e["inb"][1] <= e["right"][0], e["key"]
        assert abs(e["w"] - e["at"]) < 0.06, e["key"]
    for s in catalog["sources"].values():
        assert s["short"] and s["cite"] and (s.get("doi", "").startswith("10.") or s.get("url", "").startswith("https://")), s
    cited = {e["source"] for e in catalog["lines"] + catalog["bands"]} | {k for e in catalog["lines"] + catalog["bands"] for k in e.get("also", [])}
    assert cited == set(catalog["sources"])       # no source is listed that no entry uses


def test_hydrogen_lines_agree_with_the_rydberg_formula(catalog):
    rydberg = 109677.5834          # cm-1, for hydrogen
    hydrogen = [e for e in catalog["lines"] if e.get("h")]
    assert len(hydrogen) >= 12
    for e in hydrogen:
        lower, upper = (int(n) for n in e["transition"].split("(")[1].rstrip(")").split("–"))
        expected = 1e4 / (rydberg * (1 / lower**2 - 1 / upper**2))
        assert abs(e["w"] - expected) < 2e-5 * expected, (e["key"], e["w"], expected)   # 0.002 %: the fine structure


def test_every_feature_comes_from_the_catalogue(readings, catalog):
    known = {e["key"]: e for e in catalog["lines"] + catalog["bands"]}
    shorts = {s["short"] for s in catalog["sources"].values()}
    for name, r in readings.items():
        z = r["redshift"] or 0
        for f in r["features"]:
            e = known[f["key"]]
            assert f["rest_um"] == e["w"] and f["source"] == catalog["sources"][e["source"]]["short"], (name, f["key"])
            if f["kind"] == "emission":
                assert abs(f["wave_um"] - e["w"] * (1 + z)) < 1e-9, (name, f["key"])
        assert {f["key"] for f in r["features"]} <= {t["key"] for t in r["tested"]}, name
        for t in r["tested"]:
            assert t["source"] in shorts and t["status"] in ("detected", "possible", "absent", "unmeasured", "blended", "alone", "skipped"), (name, t)
        assert {t["source"] for t in r["tested"]} <= {s["short"] for s in r["sources"]}, name
        if r["state"] == "ok":
            assert len(r["tested"]) >= 1, name


def test_a_weak_line_alone_is_not_named(readings):
    # behind the ice bands of Elias 16 the continuum stands up near 3.74 um, where Pfund gamma would be
    r = readings["elias-16"]
    assert not [f for f in r["features"] if f["kind"] == "emission"]
    assert {t["key"]: t["status"] for t in r["tested"]}["pf_gamma"] == "alone"
    # in a nebula the same line list finds the strong lines, and tells the PAH band from the Pfund line inside it
    status = {t["key"]: t["status"] for t in readings["ngc-7027"]["tested"]}
    assert status["br_beta"] == "detected" and status["pf_delta"] == "blended"


def test_molecular_bands_stop_the_line_tests(readings):
    assert {t["status"] for t in readings["epsilon-indi-b"]["tested"] if t["kind"] == "line"} == {"skipped"}


# ---------------------------------------------------------------- with a published redshift

def test_quasar_lines_are_found_where_the_redshift_puts_them(readings):
    for name in ("pg-1634-706", "apm-08279-5255", "sdss-j0100-2802"):
        plain, shifted = readings[name], readings[name + "@z"]
        assert not features(plain), name                      # at rest wavelengths there is nothing to see
        assert shifted["metrics"]["kind"] == "emission" and "h_alpha" in features(shifted), name
        assert "published redshift" in shifted["headline"], name
        assert any("published redshift" in c for c in shifted["caveats"]), name
        assert all(t["kind"] == "line" for t in shifted["tested"]), name    # no stellar or ice band is tested
    strength = {f["key"]: f["strength"] for f in readings["pg-1634-706@z"]["features"]}
    assert 1.5 < strength["h_alpha"] < 3.0           # H alpha stands about three times above the continuum
    assert {"h_alpha", "h_beta", "pa_alpha"} <= features(readings["pg-1634-706@z"])
    assert readings["sdss-j0100-2802@z"]["headline"].startswith("An emission line")
    # 3C 273: what looked like Brackett gamma at rest is Paschen alpha at z = 0.158
    near = readings["3c-273@z"]
    assert near["metrics"]["kind"] == "emission" and {"pa_alpha", "he_i"} <= features(near)
    assert "br_gamma" not in features(near)


def test_redshift_moves_the_lines_and_a_wrong_one_finds_nothing(readings):
    assert not features(readings["shifted"])
    right = readings["shifted@z"]
    assert right["metrics"]["kind"] == "emission" and {"h_alpha", "h_beta"} <= features(right)
    alpha = next(f for f in right["features"] if f["key"] == "h_alpha")
    assert abs(alpha["wave_um"] - 3 * 0.65646) < 1e-6 and alpha["rest_um"] == 0.65646
    wrong = readings["shifted@wrong"]
    assert wrong["metrics"]["kind"] != "emission" and not features(wrong)


def test_a_redshifted_source_is_not_called_a_star(readings):
    r = readings["hot@z"]
    assert r["metrics"]["kind"] == "hot" and not r["features"]
    assert "photosphere" not in r["headline"] and "star" not in r["plain"]
    assert any(f["key"] == "redshift" for f in r["findings"])
