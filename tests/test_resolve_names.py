"""Name lookup (Sesame, then SIMBAD) against recorded answers; no network."""

import httpx
import pytest

from sphereblink import resolve

# Answers recorded from the services on 2026-11-14.
SESAME_VEGA = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
xsi:noNamespaceSchemaLocation="https://vizier.cds.unistra.fr/xml/sesame_4x.xsd">
<Target option="SNV">
<name>Vega</name>
<!-- Q157150 #1 -->
<Resolver name="S=Simbad, IDIA (via url)"><!--delay: 0ms [0] -->
<INFO>from cache</INFO>
<oid>2900336</oid>
<oname>* alf Lyr</oname>
<otype>dS*</otype>
<jpos>18:36:56.33 +38:47:01.2</jpos>
<jradeg>279.234734787</jradeg>
<jdedeg>38.783688956</jdedeg>
<refPos>2007A&amp;A...474..653V</refPos>
<errRAmas>3.51</errRAmas><errDEmas>2.81</errDEmas>
<pm>
<v>349.72</v><e>0.51</e><q>A</q><r>2007A&amp;A...474..653V</r>
<pa>35.1</pa>
<pmRA>200.94</pmRA><epmRA>0.40</epmRA>
<pmDE>286.23</pmDE><epmDE>0.32</epmDE>
</pm>
<plx><v>130.23</v><e>0.36</e><q>A</q><r>2007A&amp;A...474..653V</r></plx>
</Resolver>
</Target>
</Sesame>
<!--- ====Done (2026-Nov-14,03:32:34z)==== -->
"""

SESAME_M31 = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Target option="SNV">
<name>M31</name>
<Resolver name="S=Simbad, IDIA (via url)"><!--delay: 0ms [0] -->
<oid>1575544</oid>
<oname>M  31</oname>
<otype>AGN</otype>
<jpos>00:42:44.32 +41:16:07.5</jpos>
<jradeg>10.6847083</jradeg>
<jdedeg>41.26875</jdedeg>
<Vel><v>-300.0</v><e>4.0</e><q>C</q><r>2012AJ....144....4M</r></Vel>
</Resolver>
</Target>
</Sesame>
"""

SESAME_PROXIMA = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame>
<Target option="SNV">
<name>Proxima Centauri</name>
<Resolver name="S=Simbad, IDIA (via url)"><!--delay: 13ms [13] -->
<oname>NAME Proxima Centauri</oname>
<jradeg>217.42894222160578</jradeg>
<jdedeg>-62.67949018907555</jdedeg>
<pm>
<v>3859.228</v><e>0.060</e><q>A</q><r>2020yCat.1350....0G</r>
<pa>281.501</pa>
<pmRA>-3781.741</pmRA><epmRA>0.031</epmRA>
<pmDE>769.465</pmDE><epmDE>0.051</epmDE>
</pm>
</Resolver>
</Target>
</Sesame>
"""

SESAME_NOTHING = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Target option="SNV">
<name>zzzznotastar123</name>
<!-- Q157158 #1 -->
<INFO> *** NNNothing found *** </INFO>
</Target>
</Sesame>
<!--- ====Done (2026-Nov-14,03:33:53z)==== -->
"""

TAP_VEGA = {"metadata": [], "data": [["* alf Lyr", 279.234734787025, 38.783688956244, 200.94, 286.23]]}
CDS_DOWN = "<html><title>Page unavailable - CDS</title>404 This page is currently unavailable.</html>"


def client_for(handler):
    """A client whose requests go to ``handler``; ``client.calls`` lists the hosts asked."""
    calls = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    client.calls = calls
    return client


def sesame_answers(body):
    return client_for(lambda request: httpx.Response(200, text=body))


def test_everyday_star_name_resolves_with_proper_motion():
    c = sesame_answers(SESAME_VEGA)
    t = resolve.resolve("Vega", c)
    assert c.calls == ["cds.unistra.fr"]
    assert t.name == "Vega (* alf Lyr)"
    assert t.source == "SIMBAD"
    assert (t.ra_input, t.dec_input) == pytest.approx((279.234734787, 38.783688956))
    assert (t.pmra, t.pmdec, t.epoch) == (200.94, 286.23, 2000.0)
    assert (t.dec - t.dec_input) * 3600 == pytest.approx(0.28623 * (resolve.SPHEREX_EPOCH - 2000), rel=1e-6)


def test_name_is_sent_url_encoded():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=SESAME_PROXIMA)

    t = resolve.resolve("  Proxima   Centauri ", client_for(handler))
    assert seen == ["https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?Proxima%20Centauri"]
    assert t.name == "Proxima Centauri"
    assert t.pmra == pytest.approx(-3781.741)
    # 3.8 arcsec/yr westwards: the 2025.9 position is ~100 arcsec from the catalogue one
    assert abs(t.ra - t.ra_input) * 3600 > 150


def test_object_without_proper_motion_stays_put():
    t = resolve.resolve("M31", sesame_answers(SESAME_M31))
    assert t.name == "M 31"
    assert (t.ra, t.dec) == pytest.approx((10.6847083, 41.26875))
    assert (t.pmra, t.pmdec) == (0.0, 0.0)


def test_unknown_name_is_not_found_and_simbad_is_not_asked_again():
    c = sesame_answers(SESAME_NOTHING)
    with pytest.raises(resolve.NameNotFound, match="zzzznotastar123"):
        resolve.resolve("zzzznotastar123", c)
    assert c.calls == ["cds.unistra.fr"]


def test_redirect_to_a_mirror_is_followed():
    def handler(request):
        if request.url.host == "cds.unistra.fr":
            return httpx.Response(302, headers={"Location": "https://vizier.idia.ac.za/viz-bin/nph-sesame/-oxp/SNV?Vega"})
        return httpx.Response(200, text=SESAME_VEGA)

    c = client_for(handler)
    assert resolve.resolve("Vega", c).source == "SIMBAD"
    assert c.calls == ["cds.unistra.fr", "vizier.idia.ac.za"]


def test_second_sesame_host_is_used_when_the_first_is_down():
    def handler(request):
        if request.url.host == "cds.unistra.fr":
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, text=SESAME_VEGA)

    c = client_for(handler)
    assert resolve.resolve("Vega", c).pmdec == 286.23
    assert c.calls == ["cds.unistra.fr", "vizier.cfa.harvard.edu"]


def test_simbad_is_asked_when_sesame_is_down():
    queries = []

    def handler(request):
        if "sesame" in request.url.path:
            return httpx.Response(503)
        if request.url.host == "simbad.cds.unistra.fr":
            return httpx.Response(404, text=CDS_DOWN)
        queries.append(request.url.params["QUERY"])
        return httpx.Response(200, json=TAP_VEGA)

    c = client_for(handler)
    t = resolve.resolve("Vega", c)
    assert c.calls == ["cds.unistra.fr", "vizier.cfa.harvard.edu", "simbad.cds.unistra.fr", "simbad.cfa.harvard.edu"]
    assert "IN ('Vega', 'NAME Vega')" in queries[0]
    assert (t.name, t.source, t.pmra) == ("Vega (* alf Lyr)", "SIMBAD", 200.94)


def test_simbad_spellings_cover_catalogue_spacing_and_quotes():
    assert resolve._simbad_ids("M31") == ["M31", "NAME M31", "M 31", "M  31", "M   31"]
    assert resolve._simbad_ids("HD 172167")[:3] == ["HD 172167", "NAME HD 172167", "HD  172167"]
    seen = []

    def handler(request):
        if "sesame" in request.url.path:
            return httpx.Response(500)
        seen.append(request.url.params["QUERY"])
        return httpx.Response(200, json={"data": []})

    with pytest.raises(resolve.NameNotFound):
        resolve.resolve("Teegarden's Star", client_for(handler))
    assert "'Teegarden''s Star', 'NAME Teegarden''s Star'" in seen[0]


def test_every_service_down_is_reported_as_unavailable_not_as_unknown():
    def handler(request):
        if request.url.host.endswith("unistra.fr"):
            return httpx.Response(404, text=CDS_DOWN)
        raise httpx.ConnectError("no route", request=request)

    c = client_for(handler)
    with pytest.raises(resolve.ResolverUnavailable, match="not answering") as err:
        resolve.resolve("Vega", c)
    assert len(c.calls) == 4
    assert not isinstance(err.value, resolve.NameNotFound)
    assert isinstance(err.value, ValueError)  # the API still turns it into a clear message


def test_a_page_that_is_not_a_sesame_answer_counts_as_a_failed_service():
    def handler(request):
        if "sesame" in request.url.path:
            return httpx.Response(200, text=CDS_DOWN)
        return httpx.Response(200, text="not json")

    with pytest.raises(resolve.ResolverUnavailable):
        resolve.resolve("Vega", client_for(handler))


def test_featured_targets_and_coordinates_never_use_the_network():
    def handler(request):
        raise AssertionError("network used")

    c = client_for(handler)
    assert resolve.resolve("Barnard's  Star", c).source == "featured"
    assert resolve.resolve("eps-indi-b", c).source == "featured"
    assert resolve.resolve("269.4486 +4.7387", c).source == "coordinates"
    assert c.calls == []


def test_overlong_name_is_rejected_before_any_lookup():
    c = sesame_answers(SESAME_VEGA)
    with pytest.raises(resolve.ResolveError, match="longer than"):
        resolve.resolve("x" * 200, c)
    assert c.calls == []
