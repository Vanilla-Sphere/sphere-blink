"""The redshift read off a spectrum (web/redshift.js), on real and made-up curves.

The estimate runs in the browser, so the tests hand the spectra to the same file under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).parent
QUASARS = HERE / "data" / "redshift"
OTHERS = HERE / "data" / "spectra"
TEMPLATE = HERE.parent / "web" / "data" / "quasar-template.json"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node to run web/redshift.js")

WITH_LINES = ("iras-00040-4325", "qso-b0624-6907", "pg-1634-706", "he-0515-4414", "apm-08279-5255", "qso-j1723-2243",
              "sdss-j0100-2802", "uhs-j0439-1634", "sdss-j1528-3637")
BLAZARS = ("oj-287", "ton-599", "3c-279", "pg-1553-113")
STARS = ("barnards-star", "epsilon-indi-b", "wise-0855", "g191-b2b", "vega", "r-draconis", "rw-aurigae", "elias-16", "elias-29",
         "empty-sky", "sgr-a", "ngc-3379")

# rest wavelength (um) and height over the continuum of the lines of a made-up quasar
LINES = ((0.1549, 0.5), (0.2799, 0.35), (0.4935, 0.4), (0.6565, 1.6), (1.0856, 0.35), (1.2822, 0.2), (1.8756, 0.25))


def quasar(name):
    d = json.loads((QUASARS / f"{name}.json").read_text())
    points = [{"wave_um": w, "bandwidth_um": bw, "flux_ujy": f, "flux_err_ujy": e, "method": "aperture", "mjd": mjd, "flags": ["SOURCE"]}
              for w, bw, f, e, mjd in d["points"]]
    return d, points


def other(name):
    d = json.loads((OTHERS / f"{name}.json").read_text())
    return [{"wave_um": w, "bandwidth_um": bw, "flux_ujy": f, "flux_err_ujy": e, "method": "wings" if wings else "aperture",
             "mjd": mjd, "flags": ["EXTRAPOLATED"] if extra else []} for w, bw, f, e, wings, mjd, extra in d["points"]]


def made_up(z=None, lines=LINES, level=3000.0, noise=0.03, visits=3, per_visit=130, seed=1, flare=None):
    """A power-law continuum with emission lines at redshift z, sampled as SPHEREx does: `visits`
    passes six months apart, each of `per_visit` exposures spread over the six detectors.
    flare = (visit, wavelength): four neighbouring exposures of that visit, 60 % brighter."""
    rng = np.random.default_rng(seed)
    edges = (0.75, 1.11, 1.64, 2.42, 3.82, 4.42, 5.0)
    points = []
    for v in range(visits):
        n = per_visit // 6                                  # evenly stepped across each detector, as the survey does
        wave = np.sort(np.concatenate([lo + (hi - lo) * (np.arange(n) + rng.uniform(0, 1)) / n for lo, hi in zip(edges[:-1], edges[1:])]))
        res = np.where(wave < 3.82, 41.0, 118.0)
        flux = level * (wave / 2.0) ** 0.6
        if z is not None:
            shape = np.ones_like(wave)
            for rest, height in lines:
                at = rest * (1 + z)
                width = np.hypot(at / res / 2.3548, at * 0.006)
                shape += height * np.exp(-0.5 * ((wave - at) / width) ** 2)
            flux = flux * shape
        flux = flux * (1 + 0.05 * v)                       # a quasar is not quite as bright at each visit
        if flare and flare[0] == v:
            near = np.argsort(np.abs(wave - flare[1]))[:4]
            flux[near] *= 1.6
        err = np.full(len(wave), noise * level)
        flux = flux + rng.normal(0, err)
        # four neighbouring wavelengths of a detector are taken within minutes, the next four a day or more later
        rank, k = np.argsort(np.argsort(wave)), max(1, per_visit // 130)      # a denser visit interleaves k such series
        order = (rank // (4 * k)) * k + rank % k
        mjd = 60800.0 + 182.0 * v + rng.permutation(order.max() + 1)[order] * 1.3 + rng.uniform(0, 0.01, len(wave))
        points += [{"wave_um": round(float(w), 4), "bandwidth_um": round(float(w / r), 4), "flux_ujy": float(f), "flux_err_ujy": float(e),
                    "method": "aperture", "mjd": float(m), "flags": ["SOURCE"]} for w, r, f, e, m in zip(wave, res, flux, err, mjd)]
    return points


MADE_UP = {
    "z0.5": dict(z=0.5), "z1.2": dict(z=1.2), "z2.4": dict(z=2.4), "z3.3": dict(z=3.3), "z4.6": dict(z=4.6),
    "continuum": dict(z=None),
    "one-line": dict(z=1.0, lines=((0.6565, 1.6),)),
    "flare": dict(z=None, flare=(1, 2.0)),
    "one-visit": dict(z=2.4, visits=1, per_visit=300),
    "noisy": dict(z=2.4, noise=1.0),
}


@pytest.fixture(scope="module")
def estimates():
    spectra, known = [], {}
    for name in WITH_LINES + BLAZARS + ("lbqs-0059-0206",):
        d, points = quasar(name)
        known[name] = d["z"]
        spectra.append({"name": name, "points": points})
    for name in STARS + ("3c-273", "ngc-7027"):
        spectra.append({"name": name, "points": other(name)})
    for name, how in MADE_UP.items():
        spectra.append({"name": name, "points": made_up(**how)})
    flagged = made_up(z=2.4)
    for p in flagged:
        p["flags"] = ["SOURCE", "TRANSIENT"]
    spectra += [{"name": "flagged", "points": flagged}, {"name": "few", "points": made_up(z=2.4, per_visit=12)}, {"name": "nothing", "points": []}]
    run = subprocess.run(["node", str(HERE / "redshift_runner.js")], input=json.dumps(spectra), capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout), known


def off(z, true):
    return abs(z - true) / (1 + true)


@pytest.mark.parametrize("name", WITH_LINES)
def test_quasars_with_lines_give_their_published_redshift(estimates, name):
    found, known = estimates
    e = found[name]
    assert e["quality"] >= 3 and e["z"] is not None, (name, e["reason"])
    assert off(e["z"], known[name]) < 0.02, (name, e["z"], known[name])
    assert 0 < e["zErr"] < 0.05 * (1 + e["z"])
    assert len(e["lines"]) >= 2 and all(line["snr"] >= 3 for line in e["lines"])
    for line in e["lines"]:                                   # every line stands where the redshift puts it
        slack = 0.035 if line["id"] == "lya" else 0.02
        assert abs(line["wave"] / (line["rest"] * (1 + e["z"])) - 1) < slack, (name, line)


def test_the_brightest_quasar_of_the_early_universe(estimates):
    found, _ = estimates
    e = found["sdss-j0100-2802"]                              # z = 6.327: Ha at 4.8 um, Lya at 0.9 um
    assert {"ha", "lya"} <= {line["id"] for line in e["lines"]}
    assert 6.2 < e["z"] < 6.45


@pytest.mark.parametrize("name", BLAZARS)
def test_blazars_give_no_redshift(estimates, name):
    found, _ = estimates
    e = found[name]                                           # light that changes from day to day, and no lines
    assert e["z"] is None and e["quality"] <= 2, (name, e["z"])


def test_one_line_is_not_a_redshift(estimates):
    found, known = estimates
    e = found["lbqs-0059-0206"]                               # Ha alone, at 1.52 um
    assert e["z"] is None and e["quality"] == 2 and e["reason"] == "single"
    assert abs(e["single"]["wave"] / (0.6565 * (1 + known["lbqs-0059-0206"])) - 1) < 0.02
    e = found["one-line"]
    assert e["z"] is None and e["quality"] == 2


@pytest.mark.parametrize("name", STARS)
def test_stars_and_empty_sky_give_no_redshift(estimates, name):
    found, _ = estimates
    e = found[name]
    assert e["z"] is None, (name, e["z"], e["lines"])


def test_lines_at_rest_are_not_a_cosmological_redshift(estimates):
    found, _ = estimates
    e = found["ngc-7027"]                                     # a planetary nebula: hydrogen lines at z = 0
    assert e["z"] is None or abs(e["z"]) < 0.01
    e = found["3c-273"]                                       # as recorded for the other tests, flags not kept
    assert e["z"] is None or off(e["z"], 0.1576) < 0.02


@pytest.mark.parametrize("name,z", [("z0.5", 0.5), ("z1.2", 1.2), ("z2.4", 2.4), ("z3.3", 3.3), ("z4.6", 4.6), ("one-visit", 2.4)])
def test_made_up_quasars_come_back(estimates, name, z):
    found, _ = estimates
    e = found[name]
    assert e["quality"] >= 3, (name, e["reason"], e["peaks"])
    assert off(e["z"], z) < 0.01, (name, e["z"])
    assert "ha" in {line["id"] for line in e["lines"]}


def test_nothing_is_read_into_a_bare_continuum(estimates):
    found, _ = estimates
    assert found["continuum"]["z"] is None and found["continuum"]["quality"] == 1
    assert found["noisy"]["z"] is None


def test_a_flare_at_one_visit_is_not_a_line(estimates):
    found, _ = estimates
    e = found["flare"]                                        # four neighbouring exposures of one visit, 60 % brighter
    assert e["z"] is None
    assert not any(abs(p["wave"] - 2.0) < 0.1 for p in e["peaks"]), e["peaks"]


def test_points_that_cannot_be_read(estimates):
    found, _ = estimates
    for name in ("flagged", "few", "nothing"):
        assert found[name]["z"] is None and found[name]["quality"] == 1 and found[name]["reason"] == "few", name


def test_every_estimate_is_complete(estimates):
    found, _ = estimates
    for name, e in found.items():
        assert e["quality"] in (1, 2, 3, 4), name
        assert (e["z"] is None) == (e["quality"] < 3), name
        if e["z"] is None:
            assert e["reason"] in ("few", "faint", "nolines", "single", "weak", "disagree"), (name, e["reason"])
        else:
            assert e["zErr"] > 0 and e["match"]["strength"] >= 9, name


def test_the_template_is_what_the_estimate_expects():
    t = json.loads(TEMPLATE.read_text())
    flux = np.array(t["flux"])
    wave = t["wave0"] * np.exp(np.arange(len(flux)) * t["step"])
    assert wave[0] <= 0.1001 and wave[-1] > 3.5 and np.all(np.isfinite(flux)) and np.all(flux > 0)
    assert "Selsing" in t["source"] and "Glikman" in t["source"]
    for rest in (0.1216, 0.6565):                             # Lya and Ha stand well above what is on either side
        peak = flux[(wave > rest * 0.99) & (wave < rest * 1.01)].max()
        sides = np.median(flux[((wave > rest * 0.9) & (wave < rest * 0.94)) | ((wave > rest * 1.06) & (wave < rest * 1.1))])
        assert peak > 2 * sides, rest
