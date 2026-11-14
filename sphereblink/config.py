"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# IRSA Simple Image Access v2 endpoint and the SPHEREx collections to search,
# newest first. A new data release only needs its collection name added here
# (or in SPHEREX_COLLECTIONS) - no code change.
SIA_URL = os.environ.get("SPHEREX_SIA_URL", "https://irsa.ipac.caltech.edu/SIA")
COLLECTIONS = [
    c.strip()
    for c in os.environ.get("SPHEREX_COLLECTIONS", "spherex_dr1,spherex_qr3").split(",")
    if c.strip()
]

# Public AWS Open Data bucket that mirrors the IRSA SPHEREx archive. Reading
# from here allows HTTP range requests, so a cutout never downloads a whole
# 65 MB Level 2 file.
S3_HTTP_BASE = os.environ.get(
    "SPHEREX_S3_HTTP_BASE", "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
)

CACHE_DIR = Path(os.environ.get("SPHEREX_CACHE_DIR", ".cache/sphereblink"))
DB_URL = os.environ.get("DATABASE_URL", "")  # postgres://... ; empty = SQLite in CACHE_DIR

# Native SPHEREx pixel is 6.15 arcsec.
NATIVE_PIXEL_ARCSEC = 6.15
MAX_FOV_ARCMIN = float(os.environ.get("SPHEREX_MAX_FOV_ARCMIN", "30"))
MAX_FRAMES = int(os.environ.get("SPHEREX_MAX_FRAMES", "1200"))
FETCH_WORKERS = int(os.environ.get("SPHEREX_FETCH_WORKERS", "32"))  # concurrent exposures per view
# Processes that cut exposures. Set it to the number of CPUs the server really
# has: inside a container os.cpu_count() reports the host's cores.
FETCH_PROCS = int(os.environ.get("SPHEREX_FETCH_PROCS", str(min(4, os.cpu_count() or 1))))
# Frames of recently opened views stay in memory up to this size; older views
# are reloaded from the disk cache when someone opens them again.
RAM_BUDGET_MB = float(os.environ.get("SPHEREX_RAM_BUDGET_MB", "600"))
# The disk cache drops its least recently fetched views above this size.
DISK_BUDGET_GB = float(os.environ.get("SPHEREX_DISK_BUDGET_GB", "4"))

# Flag planes that make a pixel unusable for display. SOURCE is deliberately
# not here: it marks pixels on known stars, which is exactly what we want to see.
BAD_FLAG_NAMES = (
    "TRANSIENT",
    "OVERFLOW",
    "SUR_ERROR",
    "NONFUNC",
    "MISSING_DATA",
    "HOT",
    "COLD",
    "NONLINEAR",
    "PERSIST",
    "OUTLIER",
    "SNOWBALL",
)
