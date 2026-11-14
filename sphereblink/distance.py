"""How far away is it? The distance and the redshift of the object at a
position, each with the catalogue and the paper it comes from.

Nothing here is measured by us. A star's distance is its Gaia DR3 parallax
(with the distance Bailer-Jones et al. 2021 derive from it); a galaxy's or a
quasar's redshift is the published one that SIMBAD or NED lists, and what
follows from it (how long the light travelled, how far the object is) is
computed with the Planck 2018 cosmology and labelled as such. SPHEREx has no
public redshift catalogue yet, and a redshift read off our own quick spectra
would not be one to trust without a study of its own.

Every service has a second way in: SIMBAD two mirrors and then NED, Gaia the
ESA archive and then IRSA."""

from __future__ import annotations

import csv
import io
import math
import statistics
from concurrent.futures import ThreadPoolExecutor

import httpx

from .resolve import SIMBAD_TAP_URLS, SPHEREX_EPOCH, Target, propagate

ESA_TAP_URL = "https://gea.esac.esa.int/tap-server/tap/sync"
IRSA_TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
NED_TAP_URL = "https://ned.ipac.caltech.edu/tap/sync"
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
SLOW_TIMEOUT = httpx.Timeout(25.0, connect=5.0)

NAMED_RADIUS = 5.0  # arcsec around a catalogue position
POINT_RADIUS = 10.0  # arcsec around a point somebody picked: under two SPHEREx pixels
GAIA_RADIUS = 15.0  # wider, so a star that has moved since 2016 is still found
PIXEL = 7.5  # arcsec: Gaia stars this close to the point share its SPHEREx pixel
PARALLAX_SNR = 5.0  # below this a parallax alone does not give a distance
NO_PARALLAX_SNR = 1.5  # below this there is no parallax to speak of: a far star, or no star at all
LOCAL_Z = 0.01  # below this a galaxy's own motion rivals the expansion
C_KM_S = 299792.458
LY_PER_PC = 3.261563777

COSMOLOGY = "Planck 2018 (flat ΛCDM, H0 = 67.66 km/s/Mpc, Ωm = 0.3111)"

# SIMBAD object types that lie outside the Milky Way (its redshift is then a
# cosmological one), and those among them that are seen as a point: quasars
# and blazars. A Seyfert or LINER nucleus sits in a galaxy one can see.
QUASARS = {"QSO", "Bla", "BLL", "Q?", "Bz?", "BL?", "LeQ"}
GALAXIES = {"AGN", "Sy1", "Sy2", "SyG", "LIN", "AG?", "G", "LSB", "bCG", "SBG", "H2G", "EmG", "rG", "GiP", "GiG", "GiC", "BiC", "IG", "PaG", "GrG", "CGG", "ClG",
            "PCG", "SCG", "LeG", "LeI", "GLS", "gLe", "gLS", "G?", "SC?", "C?G", "Gr?", "LS?", "Le?", "LI?", "HzG",
            "ERO", "LyA", "DLA", "gB", "PoG"}
# Entries that sit on another object and repeat its numbers (a planet lists
# its star's parallax), or that are a region rather than a body.
NOT_THE_OBJECT = {"Pl", "Pl?", "Cld", "MoC", "PoC", "reg", "vid", "err", "Rad", "X", "IR", "NIR", "FIR", "MIR", "UV",
                  "gam", "mm", "cm", "smm", "Opt", "blu", "EmO", "?"}
OTYPES = {
    "*": "Star", "PM*": "High proper-motion star", "BD*": "Brown dwarf", "LM*": "Low-mass star", "WD*": "White dwarf",
    "V*": "Variable star", "LP*": "Long-period variable star", "Mi*": "Mira variable star", "TT*": "T Tauri star",
    "Or*": "Orion variable star", "Y*O": "Young stellar object", "RG*": "Red giant", "AB*": "AGB star",
    "C*": "Carbon star", "S*": "S-type star", "Em*": "Emission-line star", "Be*": "Be star", "RR*": "RR Lyrae variable",
    "Ce*": "Cepheid", "cC*": "Classical Cepheid", "dS*": "Delta Scuti variable", "Ro*": "Rotating variable star",
    "Er*": "Eruptive variable star", "EB*": "Eclipsing binary", "SB*": "Spectroscopic binary", "**": "Double star",
    "HB*": "Horizontal-branch star", "s*r": "Red supergiant", "s*b": "Blue supergiant", "WR*": "Wolf-Rayet star",
    "N*": "Neutron star", "Psr": "Pulsar", "PN": "Planetary nebula", "SNR": "Supernova remnant", "SN*": "Supernova",
    "HII": "HII region", "GlC": "Globular cluster", "OpC": "Open cluster", "Cl*": "Star cluster",
    "G": "Galaxy", "EmG": "Emission-line galaxy", "SBG": "Starburst galaxy", "H2G": "HII galaxy",
    "LSB": "Low surface brightness galaxy", "bCG": "Blue compact galaxy", "rG": "Radio galaxy",
    "GiC": "Galaxy in a cluster", "GiG": "Galaxy in a group", "GiP": "Galaxy in a pair",
    "BiC": "Brightest galaxy of a cluster", "IG": "Interacting galaxies", "PaG": "Pair of galaxies",
    "GrG": "Group of galaxies", "CGG": "Compact group of galaxies", "ClG": "Cluster of galaxies",
    "AGN": "Active galaxy nucleus", "SyG": "Seyfert galaxy", "Sy1": "Seyfert 1 galaxy", "Sy2": "Seyfert 2 galaxy",
    "LIN": "LINER galaxy", "QSO": "Quasar", "Bla": "Blazar", "BLL": "BL Lac object (a blazar)",
    "LeG": "Gravitationally lensed galaxy", "LeQ": "Gravitationally lensed quasar",
    "LeI": "Gravitationally lensed image", "GLS": "Gravitational lens system", "gLe": "Gravitational lens",
    "gB": "Gamma-ray burst", "G?": "Galaxy candidate", "Q?": "Quasar candidate", "AG?": "Active nucleus candidate",
}
Z_NATURE = {"s": "spectroscopic", "p": "photometric"}  # SIMBAD's rvz_nature by its first letter


class Unavailable(Exception):
    """No mirror of a service gave a usable answer."""


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _sep(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Separation of two nearby positions in arcsec."""
    return 3600.0 * math.hypot(((ra1 - ra2 + 540.0) % 360.0 - 180.0) * math.cos(math.radians(dec1)), dec1 - dec2)


def _tap(client: httpx.Client, urls, adql: str, **extra) -> list[dict]:
    """Rows of a TAP query from the first address that answers with a table."""
    errors = []
    for url in urls:
        try:
            r = client.get(url, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql, **extra},
                           timeout=TIMEOUT, follow_redirects=True)
            r.raise_for_status()
            if r.text.lstrip().startswith("<"):  # an error page, or a VOTable that says what was wrong
                raise ValueError("not a table")
            return list(csv.DictReader(io.StringIO(r.text)))
        except Exception as exc:  # the next address may still answer
            errors.append(f"{httpx.URL(url).host}: {type(exc).__name__}")
    raise Unavailable("; ".join(errors))


def kind_of(otype: str) -> str:
    """star, galaxy, quasar or other, from a SIMBAD object type."""
    o = (otype or "").strip()
    if o in QUASARS:
        return "quasar"
    if o in GALAXIES:
        return "galaxy"
    if o.endswith("*") or o in ("**", "Y*O", "s*r", "s*b", "s*y", "Psr", "PN"):
        return "star"
    return "other"


# ----------------------------------------------------------------------------- SIMBAD
# A bright galaxy holds hundreds of catalogue entries (its clusters, clouds,
# X-ray sources): the ones with a redshift or a parallax are asked for first,
# and only if there are none, whatever is nearest.
SIMBAD_CONE = (
    "SELECT TOP {top} b.oid, b.main_id, b.otype, b.ra, b.dec, b.plx_value, b.plx_err, b.plx_bibcode, b.rvz_type, "
    "b.rvz_radvel, b.rvz_redshift, b.rvz_err, b.rvz_qual, b.rvz_nature, b.rvz_bibcode, b.sp_type, b.morph_type, "
    "DISTANCE(POINT('ICRS', b.ra, b.dec), POINT('ICRS', {ra}, {dec})) AS dist "
    "FROM basic AS b WHERE CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra}, {dec}, {r})) = 1{known} ORDER BY dist"
)
SIMBAD_KNOWN = " AND (b.rvz_redshift IS NOT NULL OR b.rvz_radvel IS NOT NULL OR b.plx_value IS NOT NULL)"
SIMBAD_DISTANCES = "SELECT d.dist, d.unit, d.minus_err, d.plus_err, d.method, d.bibcode FROM mesDistance AS d WHERE d.oidref = {oid}"


def simbad_cone(client: httpx.Client, ra: float, dec: float, radius_arcsec: float) -> list[dict]:
    """What SIMBAD lists around a J2000 position, nearest first."""
    where = dict(ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_arcsec / 3600.0:.6f}")
    rows = _tap(client, SIMBAD_TAP_URLS, SIMBAD_CONE.format(top=30, known=SIMBAD_KNOWN, **where))
    if not rows:
        rows = _tap(client, SIMBAD_TAP_URLS, SIMBAD_CONE.format(top=8, known="", **where))
    out = []
    for row in rows:
        ra_s, dec_s = _f(row.get("ra")), _f(row.get("dec"))
        if ra_s is None or dec_s is None:
            continue
        text = lambda k: (row.get(k) or "").strip()  # noqa: E731
        out.append({
            "oid": text("oid"), "name": " ".join(text("main_id").split()), "otype": text("otype"),
            "ra": ra_s, "dec": dec_s, "sep_arcsec": round(_sep(ra_s, dec_s, ra, dec), 2),
            "plx": _f(row.get("plx_value")), "plx_err": _f(row.get("plx_err")), "plx_bibcode": text("plx_bibcode"),
            "rv_type": text("rvz_type"), "radvel": _f(row.get("rvz_radvel")), "z": _f(row.get("rvz_redshift")),
            "z_err": _f(row.get("rvz_err")), "z_qual": text("rvz_qual"), "z_nature": text("rvz_nature"),
            "z_bibcode": text("rvz_bibcode"), "sp_type": text("sp_type"), "morph_type": text("morph_type"),
        })
    out.sort(key=lambda s: s["sep_arcsec"])
    return out


# The featured targets under the names SIMBAD files them: a close pair
# (RW Aur A and B) is then not mistaken for one of its members.
FEATURED_IDS = {"Barnard's Star": "NAME Barnard's star", "Epsilon Indi B": "* eps Ind B", "RW Aurigae": "V* RW Aur",
                "R Draconis": "V* R Dra", "WISE 0855-0714": "WISEA J085510.74-071442.5"}


def _squash(name: str) -> str:
    return "".join(name.lower().replace("name ", "").split())


def pick(rows: list[dict], name: str = "") -> dict | None:
    """The catalogue entry a position most likely means: the one that carries
    the target's own name, or the nearest real body that has a redshift or a
    parallax, or else the nearest real body. A cloud in front of a galaxy, an
    X-ray source inside it or a planet (which lists its star's parallax) is
    passed over while a body is there."""
    wanted = {_squash(FEATURED_IDS.get(name, name))} | {_squash(part) for part in name.replace(")", "").split(" (") if part}
    bodies = [s for s in rows if s["otype"] not in NOT_THE_OBJECT]
    known = [s for s in bodies if s["z"] is not None or s["plx"] is not None]
    for group in ([s for s in bodies if name and _squash(s["name"]) in wanted], known, bodies, rows):
        if group:
            return group[0]
    return None


def simbad_distances(client: httpx.Client, oid: str) -> dict | None:
    """Distances of a galaxy measured without its redshift (Cepheids, the tip
    of the red giant branch, supernovae...), as SIMBAD collects them from the
    literature: how many, their median and range, and the newest papers."""
    rows = _tap(client, SIMBAD_TAP_URLS, SIMBAD_DISTANCES.format(oid=int(oid)))
    scale = {"pc": 1e-6, "kpc": 1e-3, "mpc": 1.0}
    found = []
    for row in rows:
        d, unit, method = _f(row.get("dist")), (row.get("unit") or "").strip().lower(), (row.get("method") or "").strip()
        if d is None or d <= 0 or unit not in scale or method.lower() == "redshift":
            continue
        found.append({"mpc": d * scale[unit], "method": method, "bibcode": (row.get("bibcode") or "").strip()})
    if not found:
        return None
    values = sorted(m["mpc"] for m in found)
    newest = sorted(found, key=lambda m: m["bibcode"][:4], reverse=True)[:3]
    return {"mpc": round(statistics.median(values), 4), "mpc_min": round(values[0], 4), "mpc_max": round(values[-1], 4),
            "count": len(values), "methods": sorted({m["method"] for m in found if m["method"]}),
            "newest": [{"mpc": round(m["mpc"], 4), "method": m["method"], "bibcode": m["bibcode"]} for m in newest],
            "source": "SIMBAD (CDS), distances collected from the literature"}


# ----------------------------------------------------------------------------- NED
NED_CONE = (
    "SELECT TOP 60 prefname, ra, dec, prefphytype, z, zunc, z_bibcode, zflag FROM NEDTAP.objdir "
    "WHERE CONTAINS(POINT('J2000', ra, dec), CIRCLE('J2000', {ra}, {dec}, {r})) = 1 AND z IS NOT NULL"
)
NED_SKIP = {"*", "**", "WD*", "V*", "C*", "Blue*", "Red*", "*Cl", "*Ass", "PN", "SNR", "HII", "Neb", "MCld", "RfN",
            "AbLS", "EmLS", "SN", "Nova"}


def ned_redshift(client: httpx.Client, ra: float, dec: float, radius_arcsec: float) -> dict | None:
    """The nearest NED object with a redshift: a spectroscopic one first."""
    rows = _tap(client, (NED_TAP_URL,), NED_CONE.format(ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_arcsec / 3600.0:.6f}"))
    found = []
    for row in rows:
        z, ra_s, dec_s = _f(row.get("z")), _f(row.get("ra")), _f(row.get("dec"))
        kind = (row.get("prefphytype") or "").strip()
        if z is None or ra_s is None or dec_s is None or kind in NED_SKIP:
            continue
        flag = (row.get("zflag") or "").strip()
        found.append({"name": (row.get("prefname") or "").strip(), "otype": kind, "z": z, "z_err": _f(row.get("zunc")),
                      "z_bibcode": (row.get("z_bibcode") or "").strip(), "flag": flag,
                      "nature": {"S": "spectroscopic", "P": "photometric"}.get(flag[:1], ""),
                      "sep_arcsec": round(_sep(ra_s, dec_s, ra, dec), 2)})
    found.sort(key=lambda s: (s["nature"] != "spectroscopic", s["sep_arcsec"]))
    return found[0] if found else None


# ----------------------------------------------------------------------------- Gaia
GAIA_COLUMNS = "source_id, ra, dec, pmra, pmdec, parallax, parallax_error, phot_g_mean_mag"
# Around the point, and further out the few stars that move fast enough to
# have come from there since 2016 (Barnard's Star has covered 100 arcsec).
ESA_CONE = ("SELECT TOP 40 " + GAIA_COLUMNS + " FROM gaiadr3.gaia_source "
            "WHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {wide})) "
            "AND (pm > 400 OR 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {r}))) ORDER BY phot_g_mean_mag")
FAST_RADIUS = 200.0  # arcsec: ten years of the fastest star
IRSA_CONE = ("SELECT TOP 40 " + GAIA_COLUMNS + " FROM gaia_dr3_source "
             "WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {r}))=1")
ESA_DISTANCE = "SELECT source_id, r_med_geo, r_lo_geo, r_hi_geo FROM external.gaiaedr3_distance WHERE source_id = {id}"


def gaia_cone(client: httpx.Client, ra: float, dec: float, radius_arcsec: float) -> list[dict]:
    """Gaia DR3 stars around a position (epoch 2016.0), brightest first: the
    ESA archive, or IRSA's copy of the catalogue when it does not answer."""
    where = dict(ra=f"{ra:.6f}", dec=f"{dec:.6f}", r=f"{radius_arcsec / 3600.0:.6f}")
    try:
        rows = _tap(client, (ESA_TAP_URL,), ESA_CONE.format(wide=f"{FAST_RADIUS / 3600.0:.6f}", **where))
    except Unavailable as first:
        try:
            r = client.get(IRSA_TAP_URL, params={"QUERY": IRSA_CONE.format(**where), "FORMAT": "CSV"}, timeout=SLOW_TIMEOUT,
                           follow_redirects=True)  # IRSA takes 4 to 20 s for this
            r.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(r.text)))
            if rows and "ra" not in rows[0]:
                raise ValueError("not a table")
        except Exception as exc:
            raise Unavailable(f"{first}; irsa.ipac.caltech.edu: {type(exc).__name__}") from exc
    out = []
    for row in rows:
        ra_s, dec_s = _f(row.get("ra")), _f(row.get("dec"))
        if ra_s is None or dec_s is None:
            continue
        out.append({"id": (row.get("source_id") or "").strip(), "ra": ra_s, "dec": dec_s,
                    "pmra": _f(row.get("pmra")) or 0.0, "pmdec": _f(row.get("pmdec")) or 0.0,
                    "plx": _f(row.get("parallax")), "plx_err": _f(row.get("parallax_error")),
                    "mag": _f(row.get("phot_g_mean_mag"))})
    out.sort(key=lambda s: 99.0 if s["mag"] is None else s["mag"])
    return out


def gaia_star(stars: list[dict], ra: float, dec: float, named: bool) -> dict | None:
    """The Gaia star a position means. Each star is first moved from 2016.0 to
    where it is in the SPHEREx images. For a named object that is the nearest
    star; for a picked point it is the brightest star in the same SPHEREx
    pixel (its light is what the picture shows there), or else the nearest."""
    for s in stars:
        now = propagate(s["ra"], s["dec"], s["pmra"], s["pmdec"], SPHEREX_EPOCH - 2016.0)
        s["sep_arcsec"] = round(_sep(now[0], now[1], ra, dec), 2)
    near = sorted((s for s in stars if s["sep_arcsec"] <= (NAMED_RADIUS if named else POINT_RADIUS)), key=lambda s: s["sep_arcsec"])
    if not near:
        return None
    if named:
        return near[0]
    same_pixel = [s for s in near if s["sep_arcsec"] <= PIXEL and s["mag"] is not None]
    return min(same_pixel, key=lambda s: s["mag"]) if same_pixel else near[0]


def gaia_distance(client: httpx.Client, source_id: str) -> dict | None:
    """The distance Bailer-Jones et al. (2021) derive from the Gaia parallax:
    the median and the 16th and 84th percentiles, in parsec."""
    rows = _tap(client, (ESA_TAP_URL,), ESA_DISTANCE.format(id=int(source_id)))
    for row in rows:
        med, lo, hi = _f(row.get("r_med_geo")), _f(row.get("r_lo_geo")), _f(row.get("r_hi_geo"))
        if med is not None:
            return {"pc": med, "pc_lo": lo, "pc_hi": hi}
    return None


def parallax_distance(plx: float | None, err: float | None) -> dict | None:
    """Distance as one over the parallax, only where the parallax stands
    PARALLAX_SNR above its error: below that the inverse is biased and the
    honest answer is "not known"."""
    if plx is None or plx <= 0 or (err is not None and err > 0 and plx / err < PARALLAX_SNR):
        return None
    out = {"pc": 1000.0 / plx}
    if err is not None and 0 < err < plx:
        out.update(pc_lo=1000.0 / (plx + err), pc_hi=1000.0 / (plx - err))
    return out


def _star_distance(d: dict, plx: float | None, err: float | None, source: str, reference: str, method: str) -> dict:
    snr = plx / err if plx is not None and err else None
    out = {"pc": round(d["pc"], 4 if d["pc"] < 100 else 1), "ly": round(d["pc"] * LY_PER_PC, 3 if d["pc"] < 100 else 0),
           "parallax_mas": plx, "parallax_err_mas": err, "parallax_snr": round(snr, 1) if snr is not None else None,
           "method": method, "source": source, "reference": reference,
           "uncertain": snr is not None and snr < PARALLAX_SNR}
    if d.get("pc_lo") is not None and d.get("pc_hi") is not None:
        out.update(pc_lo=round(d["pc_lo"], 4 if d["pc"] < 100 else 1), pc_hi=round(d["pc_hi"], 4 if d["pc"] < 100 else 1))
    return out


# ----------------------------------------------------------------------------- cosmology
_cosmo = None


def cosmology(z: float) -> dict | None:
    """What a redshift means in the Planck 2018 cosmology: how long the light
    travelled, how old the universe was when it left, and how far the object
    is now. These are computed from z, not measured."""
    global _cosmo
    if z is None or not math.isfinite(z) or z <= 0 or z > 30:
        return None
    if _cosmo is None:
        from astropy.cosmology import Planck18

        _cosmo = Planck18
    travel = float(_cosmo.lookback_time(z).value)
    comoving = float(_cosmo.comoving_distance(z).value)
    return {"lookback_gyr": round(travel, 4), "age_then_gyr": round(float(_cosmo.age(z).value), 4),
            "age_now_gyr": round(float(_cosmo.age(0).value), 3), "comoving_mpc": round(comoving, 2),
            "comoving_gly": round(comoving * LY_PER_PC / 1000.0, 4),
            "luminosity_mpc": round(float(_cosmo.luminosity_distance(z).value), 2), "cosmology": COSMOLOGY}


def _redshift(z, err, nature, quality, source, bibcode) -> dict:
    out = {"z": round(z, 6), "z_err": err, "nature": nature or "not stated", "quality": quality or "",
           "source": source, "bibcode": bibcode, "velocity_km_s": round(z * C_KM_S, 1) if abs(z) < 0.1 else None}
    out["local"] = z < LOCAL_Z  # the galaxy's own motion rivals the expansion: z is no distance here
    out["cosmology"] = cosmology(z)
    return out


# ----------------------------------------------------------------------------- the answer
def lookup(target: Target, client: httpx.Client | None = None) -> dict:
    """Identity, distance and redshift of what lies at a target.

    ``complete`` is False when a service that could have added something did
    not answer: the caller then shows what there is and asks again later."""
    named = target.source != "coordinates"
    # Catalogues list an object where it was at their own epoch: SIMBAD at
    # J2000, Gaia at 2016.0. A target with a known motion is looked up there.
    at2000 = target.position_at(2000.0) if named else (target.ra, target.dec)
    at2016 = target.position_at(2016.0) if named else (target.ra, target.dec)
    own = client is None
    client = client or httpx.Client()
    out: dict = {"object": None, "kind": "unknown", "distance": None, "redshift": None, "radial_velocity": None,
                 "measured_distance": None, "notes": [], "unavailable": [], "complete": True}

    def ask(fn, *args):
        try:
            return fn(client, *args), None
        except Unavailable as exc:
            return None, str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(ask, simbad_cone, at2000[0], at2000[1], NAMED_RADIUS if named else POINT_RADIUS)
            b = pool.submit(ask, gaia_cone, at2016[0], at2016[1], GAIA_RADIUS)
            (rows, simbad_error), (stars, gaia_error) = a.result(), b.result()
        obj = pick(rows, target.name if named else "") if rows else None
        kind = kind_of(obj["otype"]) if obj else "unknown"
        if simbad_error:
            out["unavailable"].append("SIMBAD")
        if gaia_error:
            out["unavailable"].append("Gaia DR3")
        if obj:
            out["object"] = {"name": obj["name"], "otype": obj["otype"], "type": OTYPES.get(obj["otype"], ""),
                             "sep_arcsec": obj["sep_arcsec"], "sp_type": obj["sp_type"], "morph_type": obj["morph_type"],
                             "source": "SIMBAD (CDS)"}
            out["kind"] = kind
        far = kind in ("galaxy", "quasar")
        star = gaia_star(stars, target.ra, target.dec, named) if stars and not far else None
        sure = bool(star and star["plx"] and star["plx_err"] and star["plx"] / star["plx_err"] >= PARALLAX_SNR)
        if star and not named:
            # A picked point is described by the star whose light fills its pixel: the
            # SIMBAD entry of that very star, or the Gaia designation when it has none.
            back = propagate(star["ra"], star["dec"], star["pmra"], star["pmdec"], 2000.0 - 2016.0)
            same = [s for s in rows or [] if s["otype"] not in NOT_THE_OBJECT and _sep(s["ra"], s["dec"], back[0], back[1]) < 2.0]
            if same:
                obj = min(same, key=lambda s: _sep(s["ra"], s["dec"], back[0], back[1]))
                kind = kind_of(obj["otype"])
                out["object"].update(name=obj["name"], otype=obj["otype"], type=OTYPES.get(obj["otype"], ""),
                                     sep_arcsec=star["sep_arcsec"], sp_type=obj["sp_type"], morph_type=obj["morph_type"])
            elif not obj or obj["z"] is None or kind == "star":
                obj, kind = None, "star"
                out["object"] = {"name": f"Gaia DR3 {star['id']}", "otype": "*", "type": "Star", "sep_arcsec": star["sep_arcsec"],
                                 "sp_type": "", "morph_type": "", "source": "Gaia DR3 (ESA)"}
            out["kind"] = kind

        # A redshift: of a galaxy or a quasar, or of something unclassified that
        # recedes like one. SIMBAD first; NED when SIMBAD is away or lists none,
        # unless a star with a firm parallax sits on the point.
        if obj and obj["z"] is not None and (far or (kind == "other" and obj["z"] >= 0.002)):
            out["redshift"] = _redshift(obj["z"], obj["z_err"], Z_NATURE.get(obj["z_nature"][:1], ""), obj["z_qual"],
                                        "SIMBAD (CDS)", obj["z_bibcode"])
        elif not (obj and kind == "star") and not sure:
            # (a quasar is a point of light to Gaia too: without a firm parallax, or SIMBAD's
            # word that it is a star, a Gaia source may well be one)
            ned, ned_error = ask(ned_redshift, at2000[0], at2000[1], NAMED_RADIUS if named else POINT_RADIUS)
            if ned_error and (simbad_error or far):
                out["unavailable"].append("NED")
            if ned:
                out["redshift"] = _redshift(ned["z"], ned["z_err"], ned["nature"], ned["flag"], "NED (NASA/IPAC)", ned["z_bibcode"])
                if not far:
                    out["object"] = {"name": ned["name"], "otype": ned["otype"], "type": "", "sep_arcsec": ned["sep_arcsec"],
                                     "sp_type": "", "morph_type": "", "source": "NED (NASA/IPAC)"}
                    out["kind"] = kind = "quasar" if ned["otype"] in ("QSO", "Q_Lens") else "galaxy"
                    far, obj = True, None
        if out["redshift"] and obj and obj["oid"]:
            # for a galaxy next door the redshift says little about distance: look for measured ones
            measured, error = ask(simbad_distances, obj["oid"])
            out["measured_distance"] = measured
            if error and out["redshift"]["local"]:
                out["unavailable"].append("SIMBAD distances")
        if obj and kind == "star" and (obj["radvel"] is not None or obj["z"] is not None):
            v = obj["radvel"] if obj["radvel"] is not None else obj["z"] * C_KM_S
            out["radial_velocity"] = {"km_s": round(v, 2), "source": "SIMBAD (CDS)", "bibcode": obj["z_bibcode"]}

        # A parallax: of a star.
        if not out["redshift"] and not far:
            snr = star["plx"] / star["plx_err"] if star and star["plx"] is not None and star["plx_err"] else None
            if star and snr is not None and snr < NO_PARALLAX_SNR and not (obj and kind == "star"):
                # nothing says it is a star, and it shows no parallax: a distance would be a guess
                out["notes"].append("Gaia sees a point of light here but no parallax: it is too far for a distance by parallax. "
                                    "It may be a distant star, or a quasar.")
                out["kind"] = "unknown" if not obj else out["kind"]
            elif star and star["plx"] is not None:
                better, _ = ask(gaia_distance, star["id"]) if star["id"] else (None, None)
                plain = parallax_distance(star["plx"], star["plx_err"])
                if better or plain:
                    out["distance"] = _star_distance(
                        better or plain, star["plx"], star["plx_err"], "Gaia DR3 (ESA)",
                        "Bailer-Jones et al. 2021, AJ 161, 147" if better else "Gaia Collaboration 2023, A&A 674, A1",
                        "parallax, distance by Bailer-Jones et al. (2021)" if better else "parallax (distance = 1 / parallax)")
                    out["distance"].update(gaia_id=star["id"], g_mag=star["mag"], sep_arcsec=star["sep_arcsec"])
                    if out["distance"]["uncertain"]:
                        out["notes"].append("The parallax is small against its error: this distance leans on a model of "
                                            "the Galaxy (Bailer-Jones et al. 2021), not on the parallax alone.")
                    if out["kind"] in ("unknown", "other"):
                        out["kind"] = "star"
            if out["distance"] is None and obj:
                plain = parallax_distance(obj["plx"], obj["plx_err"])
                if plain:  # too bright or too faint for Gaia: the parallax SIMBAD holds (Hipparcos, ground or space)
                    out["distance"] = _star_distance(plain, obj["plx"], obj["plx_err"], "SIMBAD (CDS)", obj["plx_bibcode"],
                                                     "parallax (distance = 1 / parallax)")
                    out["kind"] = "star" if kind == "other" else kind
            if out["distance"] is None and not out["notes"] and ((star and star["plx"] is not None) or (obj and obj["plx"] is not None)):
                out["notes"].append("A parallax is listed, but it is too small against its error to give a distance.")
    finally:
        if own:
            client.close()
    out["complete"] = not out["unavailable"]
    return out
