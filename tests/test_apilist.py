"""The list "All API endpoints" of the viewer (web/apilist.js) against the routes of the server:
every endpoint that only reads is in the panel "Link or embed this view", and none that writes."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
ABOVE = {"/api/movie.gif", "/api/spectrum"}  # the rows "GIF" and "Data" the panel always shows (writeUrl in app.js)
WRITES = {"/api/measure"}  # starts a measurement that is stored: never offered as an address to open


def listed():
    """The endpoints of the rows of apilist.js, as written there."""
    return re.findall(r'^\s*\["(/[^"]+)", "[^"]+", "[^"]+"\],$', (WEB / "apilist.js").read_text(), re.M)


def test_every_endpoint_that_reads_is_in_the_panel_and_none_that_writes():
    from sphereblink.main import app

    routes = set()
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/") or "get" not in methods:  # the vote on a moving object is a POST
            continue
        path = re.sub(r"\{[a-z_]+\}", "{id}", path)
        routes.add("/api/atlas/2mass/…" if path.startswith("/api/atlas/2mass/") else path)
    assert len(routes) > 15
    rows = {row.split("?")[0] for row in listed()}
    assert routes - WRITES - ABOVE == {row for row in rows if row.startswith("/api/")}
    assert {"/docs", "/openapi.json"} <= rows and not rows & WRITES
    app_js = (WEB / "app.js").read_text()
    assert all(f'"{path}?"' in app_js for path in ABOVE) and "ApiList.update()" in app_js


def test_the_rows_are_one_per_address_and_the_page_counts_them():
    rows = listed()
    assert len(rows) == len(set(rows)) == 22
    page = (WEB / "index.html").read_text()
    assert '<details class="apilist" id="apilist">' in page and "/api/measure" in page  # the note says what is left out
    assert page.index("/static/atlas.js") < page.index("/static/apilist.js") < page.index("/static/app.js")
