"""Find SPHEREx Level 2 exposures that cover a sky position (IRSA SIA v2)."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass

import httpx

from . import config


@dataclass
class Exposure:
    obs_id: str  # e.g. 2025W33_2C_0321_1 (one exposure of the focal plane)
    detector: int  # 1-6
    collection: str  # spherex_dr1, spherex_qr3, ...
    mjd_start: float
    mjd_end: float
    wave_min_um: float  # wavelength range covered by this detector
    wave_max_um: float
    s3_key: str  # key in the nasa-irsa-spherex bucket
    access_url: str  # IRSA download URL (fallback)
    s_region: str
    ra_center: float
    dec_center: float

    @property
    def mjd(self) -> float:
        return 0.5 * (self.mjd_start + self.mjd_end)

    @property
    def uid(self) -> str:
        return f"{self.obs_id}_D{self.detector}"

    @property
    def http_url(self) -> str:
        if self.s3_key:
            return f"{config.S3_HTTP_BASE}/{self.s3_key}"
        return self.access_url

    def to_dict(self) -> dict:
        d = asdict(self)
        d["uid"] = self.uid
        d["mjd"] = self.mjd
        return d


_DET_RE = re.compile(r"D(\d)")


def _s3_key(cloud_access: str, access_url: str) -> str:
    if cloud_access:
        try:
            return json.loads(cloud_access)["aws"]["key"]
        except (ValueError, KeyError, TypeError):
            pass
    # https://irsa.ipac.caltech.edu/ibe/data/spherex/<key>
    m = re.search(r"/ibe/data/spherex/(.+\.fits)", access_url or "")
    return m.group(1) if m else ""


def _exposures_of(rows, collection: str) -> list[Exposure]:
    out: list[Exposure] = []
    for row in rows:
        if str(row["dataproduct_subtype"]).strip() not in ("image", "science"):
            continue
        if str(row["dataproduct_type"]).strip() != "image":
            continue
        band = str(row["energy_bandpassname"])
        m = _DET_RE.search(band)
        if not m:
            continue
        access_url = str(row["access_url"])
        out.append(
            Exposure(
                obs_id=str(row["obs_id"]).strip(),
                detector=int(m.group(1)),
                collection=str(row["obs_collection"]).strip() or collection,
                mjd_start=float(row["t_min"]),
                mjd_end=float(row["t_max"]),
                wave_min_um=float(row["em_min"]) * 1e6,
                wave_max_um=float(row["em_max"]) * 1e6,
                s3_key=_s3_key(str(row["cloud_access"]), access_url),
                access_url=access_url,
                s_region=str(row["s_region"]),
                ra_center=float(row["s_ra"]),
                dec_center=float(row["s_dec"]),
            )
        )
    return out


def parse_sia_csv(payload: bytes, collection: str) -> list[Exposure]:
    """The archive's CSV answer: the same columns as the VOTable at under half
    the size, and read in a few milliseconds instead of ~0.2 s. That matters
    for a wide view, which asks at 9 or 25 points for each data release."""
    return _exposures_of(csv.DictReader(io.StringIO(payload.decode("utf-8"))), collection)


def parse_sia_votable(payload: bytes, collection: str) -> list[Exposure]:
    from astropy.io.votable import parse_single_table

    table = parse_single_table(io.BytesIO(payload)).to_table(use_names_over_ids=True)
    return _exposures_of(table, collection)


def parse_sia(payload: bytes, content_type: str, collection: str) -> list[Exposure]:
    if "csv" in content_type:
        return parse_sia_csv(payload, collection)
    return parse_sia_votable(payload, collection)  # a service that ignores RESPONSEFORMAT


def find_exposures(
    ra: float,
    dec: float,
    collections: list[str] | None = None,
    client: httpx.Client | None = None,
    timeout: float = 90.0,
) -> list[Exposure]:
    """All exposures whose footprint contains (ra, dec), oldest first.

    When the same exposure/detector appears in several releases, the release
    listed first in ``collections`` wins (newest processing by default).
    """
    return find_exposures_at([(ra, dec)], collections, client, timeout)


def find_exposures_at(
    points: list[tuple[float, float]],
    collections: list[str] | None = None,
    client: httpx.Client | None = None,
    timeout: float = 90.0,
    parallel: int = 12,
) -> list[Exposure]:
    """All exposures whose footprint contains at least one of ``points``."""
    from concurrent.futures import ThreadPoolExecutor

    collections = collections or config.COLLECTIONS
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def query(arg: tuple[str, tuple[float, float]]) -> list[Exposure]:
        coll, (ra, dec) = arg
        last: Exception | None = None
        for _ in range(2):  # the archive occasionally drops one of many parallel queries
            try:
                r = client.get(
                    config.SIA_URL,
                    params={"COLLECTION": coll, "POS": f"circle {ra} {dec} 0.0005", "RESPONSEFORMAT": "csv"},
                )
                r.raise_for_status()
                return parse_sia(r.content, r.headers.get("content-type", ""), coll)
            except httpx.HTTPError as exc:
                last = exc
        raise last  # type: ignore[misc]

    try:
        jobs = [(coll, p) for coll in collections for p in points]  # release order = priority
        if len(jobs) == 1:
            found = [query(jobs[0])]
        else:
            with ThreadPoolExecutor(min(parallel, len(jobs))) as ex:
                found = list(ex.map(query, jobs))
        seen: dict[str, Exposure] = {}
        for exps in found:
            for e in exps:
                seen.setdefault(e.uid, e)
        return sorted(seen.values(), key=lambda e: (e.mjd, e.detector))
    finally:
        if own:
            client.close()
