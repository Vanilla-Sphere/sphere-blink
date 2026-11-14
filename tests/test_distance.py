"""Distance and redshift lookup against recorded catalogue answers; no network.

tests/data/distance_recorded.json holds what SIMBAD, NED, the ESA Gaia archive
and IRSA answered on 2026-11-14, keyed by host and query."""

import json
from pathlib import Path

import httpx
import pytest

from sphereblink import distance, resolve

ANSWERS = json.loads((Path(__file__).parent / "data" / "distance_recorded.json").read_text())["answers"]


def client(block=()):
    """A client that replays the recorded answers; hosts in ``block`` are down."""
    def handler(request: httpx.Request) -> httpx.Response:
        if any(b in request.url.host for b in block):
            return httpx.Response(503, text="down")
        hit = ANSWERS.get(f"{request.url.host}|{dict(request.url.params).get('QUERY', '')}")
        return httpx.Response(hit[0], text=hit[1]) if hit else httpx.Response(500, text="not recorded")
    return httpx.Client(transport=httpx.MockTransport(handler))


def look(text, block=()):
    return distance.lookup(resolve.resolve(text), client(block))


def test_a_star_gets_its_gaia_distance():
    out = look("barnard")
    assert out["kind"] == "star" and out["complete"]
    assert out["object"]["name"] == "NAME Barnard's star"
    d = out["distance"]
    assert d["pc"] == pytest.approx(1.828, abs=0.001) and d["ly"] == pytest.approx(5.96, abs=0.01)
    assert d["source"].startswith("Gaia DR3") and "Bailer-Jones" in d["reference"] and not d["uncertain"]
    assert out["redshift"] is None
    assert out["radial_velocity"]["km_s"] == pytest.approx(-110.1, abs=0.1)


def test_a_star_too_bright_for_gaia_gets_the_parallax_simbad_holds():
    out = look("279.23473 38.78369")  # Vega
    assert out["kind"] == "star" and out["object"]["name"] == "* alf Lyr"
    assert out["distance"]["pc"] == pytest.approx(7.68, abs=0.01)
    assert out["distance"]["source"] == "SIMBAD (CDS)" and out["distance"]["reference"] == "2007A&A...474..653V"


def test_a_quasar_gets_its_published_redshift_and_no_parallax_distance():
    out = look("187.27792 2.05239")  # 3C 273
    assert out["kind"] == "quasar" and out["object"]["name"] == "3C 273"
    z = out["redshift"]
    assert z["z"] == pytest.approx(0.1576, abs=0.0005) and z["nature"] == "spectroscopic" and z["source"] == "SIMBAD (CDS)"
    assert z["bibcode"] and not z["local"]
    assert z["cosmology"]["lookback_gyr"] == pytest.approx(2.04, abs=0.02)
    assert "Planck 2018" in z["cosmology"]["cosmology"]
    assert out["distance"] is None  # Gaia sees it as a point of light, but it is no star


def test_a_galaxy_next_door_is_marked_local_and_gets_measured_distances():
    out = look("40.6697 -0.0111")  # in the disc of M 77, 8 arcsec from its centre
    assert out["kind"] == "galaxy" and out["object"]["name"] == "M 77"
    assert out["redshift"]["local"] and out["redshift"]["velocity_km_s"] == pytest.approx(1044, abs=5)
    m = out["measured_distance"]
    assert m["count"] >= 2 and 8 < m["mpc"] < 20


def test_empty_sky_is_unknown_not_an_error():
    out = look("10.0 20.0")
    assert out["kind"] == "unknown" and out["object"] is None
    assert out["distance"] is None and out["redshift"] is None and out["complete"]


def test_ned_answers_when_simbad_is_down():
    out = look("187.27792 2.05239", block=("simbad",))
    assert out["unavailable"] == ["SIMBAD"] and not out["complete"]
    assert out["kind"] == "quasar" and out["redshift"]["source"] == "NED (NASA/IPAC)"
    assert out["redshift"]["z"] == pytest.approx(0.158, abs=0.001)
    assert out["distance"] is None  # and not the "distance" a zero parallax would give


def test_irsa_answers_for_gaia_when_the_esa_archive_is_down():
    out = look("barnard", block=("esac.esa.int",))
    assert out["distance"]["pc"] == pytest.approx(1.828, abs=0.001)
    assert out["distance"]["gaia_id"] == "4472832130942575872"


def test_everything_down_says_so():
    out = look("barnard", block=("simbad", "esa", "irsa", "ned"))
    assert not out["complete"] and "SIMBAD" in out["unavailable"] and "Gaia DR3" in out["unavailable"]
    assert out["distance"] is None and out["redshift"] is None


def test_kinds():
    assert distance.kind_of("QSO") == "quasar" and distance.kind_of("Sy1") == "galaxy" and distance.kind_of("G") == "galaxy"
    assert distance.kind_of("BY*") == "star" and distance.kind_of("*") == "star"


def test_pick_skips_planets_and_prefers_the_named_object():
    row = dict(oid="1", ra=0.0, dec=0.0, plx=100.0, plx_err=0.1, plx_bibcode="", rv_type="", radvel=None, z=None, z_err=None,
               z_qual="", z_nature="", z_bibcode="", sp_type="", morph_type="")
    planet = dict(row, name="HD 1 b", otype="Pl", sep_arcsec=0.0)
    star = dict(row, name="HD 1", otype="*", sep_arcsec=0.1)
    pair = dict(row, name="V* RW Aur B", otype="TT*", sep_arcsec=0.05)
    whole = dict(row, name="V* RW Aur", otype="TT*", sep_arcsec=0.7)
    assert distance.pick([planet, star])["name"] == "HD 1"
    assert distance.pick([pair, whole], "RW Aurigae")["name"] == "V* RW Aur"


def test_parallax_rules():
    assert distance.parallax_distance(100.0, 0.5)["pc"] == pytest.approx(10.0)
    assert distance.parallax_distance(1.0, 0.5) is None  # two sigma: no distance from this alone
    assert distance.parallax_distance(-0.2, 0.1) is None
    assert distance.parallax_distance(None, None) is None


def test_cosmology():
    assert distance.cosmology(0.0) is None and distance.cosmology(-0.001) is None
    c = distance.cosmology(6.0)
    assert c["lookback_gyr"] == pytest.approx(12.87, abs=0.05) and c["age_then_gyr"] == pytest.approx(0.92, abs=0.03)
    assert distance.cosmology(1.0)["comoving_mpc"] == pytest.approx(3395, rel=0.01)


def test_api_object_keeps_complete_answers_only(monkeypatch):
    from fastapi.testclient import TestClient

    from sphereblink import main

    calls = []

    def fake(t):
        calls.append(t.name)
        return {"object": None, "kind": "unknown", "distance": None, "redshift": None, "radial_velocity": None,
                "measured_distance": None, "notes": [], "unavailable": [] if len(calls) > 1 else ["SIMBAD"], "complete": len(calls) > 1}

    monkeypatch.setattr(main.distance, "lookup", fake)
    monkeypatch.setattr(main.guard, "charge", lambda *a, **k: None)
    kept = {}
    monkeypatch.setattr(main.svc.store, "get_catalog", kept.get)
    monkeypatch.setattr(main.svc.store, "put_catalog", kept.__setitem__)
    web = TestClient(main.app)
    url = "/api/object?ra=11.11111&dec=-22.22222"
    first = web.get(url)
    assert first.status_code == 200 and not first.json()["complete"] and first.headers["cache-control"] == "no-store"
    second = web.get(url)
    assert second.json()["complete"] and second.json()["target"]["ra"] == pytest.approx(11.11111)
    web.get(url)
    assert len(calls) == 2  # the complete answer came from the store the third time
    assert web.get("/api/object").status_code == 400


def test_the_list_of_the_redshift_layer_is_what_the_viewer_expects():
    """web/data/redshift.json (scripts/build_redshift.py): rows by rising z, each with its paper."""
    d = json.loads((Path(__file__).parent.parent / "web" / "data" / "redshift.json").read_text())
    rows = d["rows"]
    assert d["columns"] == ["ra", "dec", "z", "kind", "K", "name", "ref"] and len(rows) > 5000
    assert all(0 <= r[0] < 360 and -90 <= r[1] <= 90 and r[3] in "gq" and r[5] and 0 <= r[6] < len(d["refs"]) for r in rows)
    assert [r[2] for r in rows] == sorted(r[2] for r in rows) and rows[0][2] >= 0.002
    far = [r for r in rows if r[2] >= 5]
    assert len(far) > 3000 and not any(r[4] is not None and r[4] < 15 for r in far)  # no pipeline misfit left in
    assert sum(1 for r in rows if not d["refs"][r[6]]) < len(rows) / 50


def test_a_misprinted_velocity_gives_way_to_the_other_papers():
    # tau Sco as SIMBAD held it on 2026-11-14: fifteen papers, one of them at -650 km/s, and that one listed first
    tau_sco = [1.2, 3.7, -1.3, 3.0, 3.0, 4.0, 2.0, 2.0, 1.7, -0.7, 2.0, -650.47, -0.932, 1.4, -5.9]
    assert distance.settled_velocity(-650.47, tau_sco) == {"km_s": 1.7, "count": 15}
    # Barnard's star does move at 110 km/s: the papers agree, and its listed velocity stays
    barnard = [-136.6, -111.4, -111.0, -110.6, -110.5, -110.4, -110.11, -108.0, -107.0, -106.8, -106.8, -106.6, -103.0, -0.4]
    assert distance.settled_velocity(-110.11, barnard) is None
    assert distance.settled_velocity(366.0, [366.0]) is None                 # one paper: nothing to compare with
    assert distance.settled_velocity(250.0, [-240.0, -100.0, 0.0, 120.0, 250.0, 260.0]) is None      # a close pair of stars: no outlier


def test_published_velocities_are_read_from_simbad():
    def handler(request):
        assert "mesVelocities" in dict(request.url.params)["QUERY"] and "oidref = 2406927" in dict(request.url.params)["QUERY"]
        return httpx.Response(200, text='velvalue,veltype,bibcode\n2.0,"v  ","1979IAUS...30...57E"\n1.7,"V  ","2006AstL...32..759G"\n'
                                        '-0.0022,"z  ","x"\n,"v  ","y"\n')
    assert distance.simbad_velocities(httpx.Client(transport=httpx.MockTransport(handler)), "2406927") == [2.0, 1.7]
