"""The first page of the site (web/landing.html) and the move of the viewer to /sky."""

import re
from pathlib import Path

import pytest

from sphereblink import main

WEB = Path(__file__).resolve().parent.parent / "web"
PAGE = (WEB / "landing.html").read_text()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    return TestClient(main.app, follow_redirects=False)


def test_the_address_of_the_site_is_the_first_page_and_the_viewer_is_at_sky(client):
    first, viewer = client.get("/"), client.get("/sky")
    assert first.status_code == viewer.status_code == 200
    assert first.text == PAGE and "/static/app.js" not in first.text
    assert viewer.text == (WEB / "index.html").read_text() == client.get("/sky/").text
    for r in (first, viewer):
        assert r.headers["cache-control"] == "no-cache"  # a new release is seen at the next visit
    assert client.head("/sky").status_code == 200


def test_a_link_to_a_view_made_before_the_move_still_opens_that_view(client):
    """Until 2026-11-15 a view was "/?target=..." or "/?ra=...&dec=...". Such
    links are in the README, on the organisation page, in chats and in
    bookmarks: they are sent on to /sky with everything they carry."""
    for query in ("target=barnard", "target=Barnard%27s%20Star&fov=8", "ra=248.34&dec=-16.98&levels=3&mover=VS-60894-B5F6&ui=adv",
                  "ra=133.76010&dec=-7.24227&fov=8&det=5,6&group=visit", "target=r-dra&plot=lc&ui=adv", "ui=adv"):
        r = client.get("/?" + query)
        assert r.status_code == 307 and r.headers["location"] == "/sky?" + query, query
    # anything else is the first page: a shared link often arrives with a parameter of the network it was shared on
    for query in ("", "fbclid=IwAR0", "utm_source=x&utm_medium=social", "ref=producthunt"):
        r = client.get("/?" + query)
        assert r.status_code == 200 and r.text == PAGE, query


def test_the_three_lists_of_view_parameters_are_the_same():
    """The server sends old links on (main.py), the first page does the same on
    the static dev site (landing.js), and both must know every parameter the
    viewer reads from its address."""
    script = (WEB / "landing.js").read_text()
    in_page = re.search(r"const VIEW_KEYS = \[(.*?)\];", script).group(1)
    assert tuple(re.findall(r'"(\w+)"', in_page)) == main.VIEW_KEYS
    read = set()
    for name in ("app.js", "movers.js"):
        read |= set(re.findall(r'\b[pq]\.get\("(\w+)"\)', (WEB / name).read_text()))
    assert read and read <= set(main.VIEW_KEYS), read - set(main.VIEW_KEYS)


def test_the_first_page_loads_nothing_from_another_site_and_has_no_inline_script():
    """It is held to the policy of the viewer (guard.CSP): scripts, styles,
    fonts and pictures from this site only, no script in the page itself."""
    assert not re.search(r"<script(?![^>]*\bsrc=)", PAGE) and not re.search(r"\son\w+=", PAGE)
    loaded = re.findall(r'<(?:script|img|link|source|video|audio|iframe)\b[^>]*?\b(?:src|href)="([^"]+)"', PAGE)
    assert len(loaded) > 20
    for address in loaded:
        if address == "https://vanillasphere.com/":  # the canonical address, a statement and not a request
            continue
        assert address.startswith("/static/"), address
        assert (WEB / address[len("/static/"):].split("?")[0]).is_file(), address
    for name in ("landing.css", "landing.js"):
        text = (WEB / name).read_text()
        elsewhere = set(re.findall(r"https?://[^\s\"')]+", text))
        assert elsewhere <= ({"https://www.youtube-nocookie.com/embed/"} if name == "landing.js" else set()), elsewhere
        for address in re.findall(r"url\(([^)]+)\)", text):
            assert (WEB / address.strip("\"'")[len("/static/"):]).is_file(), address
    assert "fetch(" not in (WEB / "landing.js").read_text()  # a visitor who only reads the page costs the engine nothing


def test_the_video_asks_nothing_of_youtube_before_play_and_the_policy_allows_only_its_player():
    """The picture of the player is a file of this site and a plain link to the
    video. The script puts the player in its place on a press, from the one
    address the policy of the pages allows as a frame."""
    from sphereblink import guard

    poster = re.search(r'<a class="poster"[^>]*>', PAGE).group(0)
    video = re.search(r'data-video="([\w-]{11})"', poster).group(1)
    assert f'href="https://www.youtube.com/watch?v={video}"' in poster
    assert "<iframe" not in PAGE
    script = (WEB / "landing.js").read_text()
    assert '"https://www.youtube-nocookie.com/embed/" + poster.dataset.video' in script
    frames = [d.strip() for d in guard.CSP.split(";") if d.strip().startswith("frame-src")]
    assert frames == ["frame-src https://www.youtube-nocookie.com"]


def test_the_links_of_the_first_page_lead_somewhere():
    links = re.findall(r'<a\b[^>]*?\bhref="([^"]+)"', PAGE)
    ids = set(re.findall(r'\bid="([\w-]+)"', PAGE))
    views = 0
    for href in links:
        href = href.replace("&amp;", "&")
        if href.startswith("#"):
            assert href[1:] in ids, href
        elif href.startswith("/sky"):
            views += 1
            keys = [pair.split("=")[0] for pair in href.partition("?")[2].split("&") if pair]
            assert set(keys) <= set(main.VIEW_KEYS), href
        else:
            assert href == "/" or href.startswith(("/docs", "/api/", "https://github.com/Vanilla-Sphere/sphere-blink",
                                                   "https://www.youtube.com/watch?v=Nhfgcw2geB4", "https://www.youtube.com/@VanillaSphere")), href
    assert views >= 12 and PAGE.count('href="/sky"') >= 3  # the way into the viewer, top, first screen and end


def test_the_typefaces_can_write_the_first_page():
    """Hanken Grotesk and Geist Mono have no sigma, no arrows and no Vietnamese
    letters; a letter from another typeface in the middle of a line shows. The
    page writes "sigma", draws its arrows, and writes the names of the team
    without their marks, as the team's English material does."""
    text = re.sub(r"<[^>]+>", " ", PAGE)
    for letter in "σ→←↗↓★":
        assert letter not in text, letter
    vietnamese = lambda ch: 0x0102 <= ord(ch) <= 0x0103 or 0x0110 <= ord(ch) <= 0x0111 or 0x01A0 <= ord(ch) <= 0x01B0 or 0x1EA0 <= ord(ch) <= 0x1EF9
    assert not [ch for ch in text if vietnamese(ch)]


def test_the_team_is_named_with_what_each_one_did():
    """Five members, each with a name and a role; where one works or studies is
    a third, smaller line that a member may not have."""
    people = re.search(r'<ol class="people[^>]*>(.*?)</ol>', PAGE, re.S).group(1)
    rows = re.findall(r"<li>(.*?)</li>", people, re.S)
    assert len(rows) == 5
    names = [re.search(r"<b>([^<]+)</b>", row).group(1) for row in rows]
    assert len(set(names)) == 5 and all(name.isascii() for name in names)
    assert all(re.search(r"<i>[^<]+</i>", row) for row in rows)
    assert all(len(re.findall(r"<small>[^<]+</small>", row)) <= 1 for row in rows)


def test_the_sky_of_the_first_page_is_a_photograph():
    """Behind the first screen, the video and the last screen is the sky as
    SPHEREx sees it, not a drawing: files of ours, credited on the page."""
    for name, size in (("sky-milky-way", 400_000), ("sky-milky-way-m", 150_000), ("sky-orion", 250_000), ("sky-rho-oph", 200_000)):
        assert f"/static/landing/{name}.webp" in PAGE, name
        assert 20_000 < (WEB / "landing" / f"{name}.webp").stat().st_size < size, name
    assert "<canvas class=\"sky\"" not in PAGE and "NASA/JPL-Caltech/IPAC" in PAGE
    assert "1.25, 2.19 and 3.29" in PAGE  # the three colours, as scripts/landing_sky.py takes them
    assert "FRAMES = (62, 46, 22)" in (WEB.parent / "scripts" / "landing_sky.py").read_text()


def test_the_viewer_knows_its_new_address():
    page = (WEB / "index.html").read_text()
    assert 'href="/sky" aria-label="Vanilla Sphere, sky map"' in page  # the logo of the viewer stays in the viewer
    assert '<a href="/" data-hint="tip.home">' in page and '"tip.home"' in (WEB / "i18n.js").read_text()
    assert 'base + "/sky?"' in (WEB / "app.js").read_text()  # "Link to this view"
