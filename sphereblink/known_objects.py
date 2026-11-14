"""Which of the moving objects found are known asteroids or comets.

The blind search (``movers.py``) knows nothing of catalogues. Afterwards each
track is compared with the solar system objects predicted for its place and
time: a match gives the track a name, and no match leaves a candidate nobody
has listed there. Two services answer the question; the second is asked for the
tracks the first has no name for, and for all of them when the first does not
answer:

* SkyBoT cone search (IMCCE, Observatoire de Paris);
* JPL Small-Body Identification API (NASA/JPL Solar System Dynamics).

Both are asked for the whole field once per pass of the telescope, at one
time; the rates they give carry each object to the time of each track.
Positions are for the centre of the Earth. SPHEREx flies 700 km above it, so
a nearby object is seen a little off its predicted place: the faster an
object moves, the further off it may be and still match, as long as its
motion agrees.
"""

from __future__ import annotations

import logging
import math
import re

import httpx

from . import movers

log = logging.getLogger(__name__)

JPL_URL = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
SKYBOT_URL = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
TIMEOUT = httpx.Timeout(25.0, connect=6.0)
MATCH_ARCSEC = 30.0  # a slow, distant object must be this close to its prediction
MATCH_NEAR_ARCSEC = 300.0  # a fast, nearby one: parallax from low Earth orbit
RATE_TOLERANCE = 0.25  # relative difference in motion that still is the same object
RATE_FLOOR = 4.0  # arcsec per hour


# ----------------------------------------------------------------------------- reading the answers
def _sexagesimal(text: str) -> float:
    parts = [p for p in re.split(r"[^0-9.+-]+", text.strip()) if p]
    sign = -1.0 if text.strip().startswith("-") else 1.0
    values = [abs(float(p)) for p in parts[:3]] + [0.0, 0.0]
    return sign * (values[0] + values[1] / 60.0 + values[2] / 3600.0)


def parse_jpl(payload: dict, kind: str) -> list[dict]:
    """Objects of a JPL SB-ident answer: name, position (degrees), rates on the sky (arcsec per hour)."""
    fields = payload.get("fields_second") or payload.get("fields_first") or []
    rows = payload.get("data_second_pass") or payload.get("data_first_pass") or []

    def col(prefix: str) -> int | None:
        return next((i for i, f in enumerate(fields) if f.startswith(prefix)), None)

    i_name, i_ra, i_dec, i_v = col("Object name"), col("Astrometric RA"), col("Astrometric Dec"), col("Visual magnitude")
    i_dra, i_ddec = col("RA rate"), col("Dec rate")
    out = []
    if None in (i_name, i_ra, i_dec, i_dra, i_ddec):
        return out
    for r in rows:
        try:
            vmag = float(r[i_v].rstrip("NTnt")) if i_v is not None and r[i_v] not in ("", "n.a.") else None
        except ValueError:
            vmag = None
        try:
            out.append({"name": r[i_name].strip(), "ra": _sexagesimal(r[i_ra]) * 15.0, "dec": _sexagesimal(r[i_dec]),
                        "v_east": float(r[i_dra]), "v_north": float(r[i_ddec]), "vmag": vmag, "kind": kind,
                        "source": "JPL Small-Body Identification"})
        except (ValueError, IndexError):
            continue
    return out


def parse_skybot(payload) -> list[dict]:
    out = []
    for o in payload if isinstance(payload, list) else []:
        try:
            name = f'{o["Num"]} {o["Name"]}' if o.get("Num") else str(o["Name"])
            cls = str(o.get("Class") or "")
            out.append({"name": name, "ra": _sexagesimal(o["RA (hms)"]) * 15.0, "dec": _sexagesimal(o["DEC (dms)"]),
                        "v_east": float(o["dRA (arcsec/h)"]), "v_north": float(o["dDEC (arcsec/h)"]),
                        "vmag": float(o["VMag (mag)"]) if o.get("VMag (mag)") is not None else None,
                        "kind": "comet" if cls.lower().startswith("comet") else "asteroid", "class": cls,
                        "source": "SkyBoT (IMCCE)"})
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ----------------------------------------------------------------------------- asking
def _calendar(mjd: float) -> str:
    from datetime import datetime, timedelta, timezone

    t = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=mjd)
    return t.strftime("%Y-%m-%d_%H:%M:%S")


def _dashed(value: float, hours: bool) -> str:
    v = abs(value) / 15.0 if hours else abs(value)
    a = int(v)
    b = int((v - a) * 60.0)
    c = int(round(((v - a) * 60.0 - b) * 60.0))
    if c == 60:
        b, c = b + 1, 0
    if b == 60:
        a, b = a + 1, 0
    return ("M" if value < 0 and not hours else "") + f"{a:02d}-{b:02d}-{c:02d}"


def ask_jpl(client: httpx.Client, ra: float, dec: float, mjd: float, half_deg: float) -> list[dict]:
    out = []
    for kind, code in (("asteroid", "a"), ("comet", "c")):
        params = {"sb-kind": code, "mpc-code": "500", "obs-time": _calendar(mjd), "fov-ra-center": _dashed(ra, True),
                  "fov-dec-center": _dashed(dec, False), "fov-ra-hwidth": f"{half_deg:.3f}", "fov-dec-hwidth": f"{half_deg:.3f}",
                  "two-pass": "true", "suppress-first-pass": "true", "req-elem": "false"}
        r = client.get(JPL_URL, params=params)
        r.raise_for_status()
        out += parse_jpl(r.json(), kind)
    return out


def ask_skybot(client: httpx.Client, ra: float, dec: float, mjd: float, half_deg: float) -> list[dict]:
    params = {"-ep": f"{mjd + 2400000.5:.5f}", "-ra": f"{ra:.5f}", "-dec": f"{dec:.5f}", "-rd": f"{half_deg * 1.42:.3f}",
              "-mime": "json", "-output": "all", "-loc": "500", "-filter": "120", "-objFilter": "111", "-from": "VanillaSphere"}
    r = client.get(SKYBOT_URL, params=params)
    r.raise_for_status()
    return parse_skybot(r.json())


# SkyBoT first: it answers in seconds and lists 3I/ATLAS. JPL's identification takes minutes for the
# asteroids of a field (it often runs into the timeout) and has no record of 3I/ATLAS.
SOURCES = (("SkyBoT", ask_skybot), ("JPL", ask_jpl))


def predicted(ra: float, dec: float, mjd: float, half_deg: float, client: httpx.Client, skip: tuple = ()) -> tuple[list[dict], str]:
    """Known objects in a square of the sky at a time, from the first service
    that answers and is not in ``skip``, and its name ("" when none did)."""
    for name, ask in SOURCES:
        if name in skip:
            continue
        try:
            return ask(client, ra, dec, mjd, half_deg), name
        except Exception as exc:
            log.warning("known objects: %s did not answer (%s)", name, type(exc).__name__)
    return [], ""


# ----------------------------------------------------------------------------- matching
def match(track, objects: list[dict], mjd: float) -> dict | None:
    """The known object a track is, if any. ``objects`` are predictions for ``mjd``."""
    t = (track.mjd0 - mjd) * 24.0  # hours from the prediction to the track's reference time
    best, best_sep = None, float("inf")
    for o in objects:
        e, n = movers.project(o["ra"], o["dec"], track.ra0, track.dec0)
        sep = math.hypot(float(e) + o["v_east"] * t, float(n) + o["v_north"] * t)
        rate = math.hypot(o["v_east"], o["v_north"])
        dv = math.hypot(o["v_east"] - track.v_east, o["v_north"] - track.v_north)
        if dv > max(RATE_FLOOR, RATE_TOLERANCE * max(rate, track.rate)):
            continue
        limit = MATCH_NEAR_ARCSEC if rate >= movers.VERY_FAST_RATE else MATCH_ARCSEC + 2.0 * rate * 0.05
        if sep <= limit and sep < best_sep:
            best, best_sep = o, sep
    if best is None:
        return None
    return {"name": best["name"], "kind": best["kind"], "source": best["source"], "sep_arcsec": round(best_sep, 1),
            "vmag": best.get("vmag"), "rate_arcsec_hr": round(math.hypot(best["v_east"], best["v_north"]), 2),
            **({"class": best["class"]} if best.get("class") else {})}


def identify(tracks: list, margin_deg: float = 0.15) -> dict[int, dict]:
    """Names for the tracks that are known objects: {index of the track: match}.

    A track nobody answered for gets ``{"checked": False}``, so that "not
    known" is never said without having asked.
    """
    out: dict[int, dict] = {}
    if not tracks:
        return out
    groups: list[list[int]] = []
    for i in sorted(range(len(tracks)), key=lambda k: tracks[k].mjd0):
        if groups and tracks[i].mjd0 - tracks[groups[-1][0]].mjd0 < 0.5:
            groups[-1].append(i)
        else:
            groups.append([i])
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "VanillaSphere"}) as client:
        for group in groups:
            mjd = float(sum(tracks[i].mjd0 for i in group) / len(group))
            # where each object of the group was at the one time that is asked about
            places = [tuple(float(v) for v in tracks[i].at(mjd)) for i in group]
            ra0, dec0 = places[0]
            reach = 0.0
            for ra, dec in places:
                e, n = movers.project(ra, dec, ra0, dec0)
                reach = max(reach, abs(float(e)) / 3600.0, abs(float(n)) / 3600.0)
            # the services do not list the same objects: what the first does not know is asked of the next
            left, asked = list(group), []
            while left:
                objects, source = predicted(ra0, dec0, mjd, min(2.0, reach + margin_deg), client, skip=tuple(asked))
                if not source:
                    break
                asked.append(source)
                for i in list(left):
                    m = match(tracks[i], objects, mjd)
                    if m:
                        out[i] = {"checked": True, "by": source, **m}
                        left.remove(i)
            for i in left:
                out[i] = {"checked": True, "by": " + ".join(asked), "name": None} if asked else {"checked": False}
    return out
