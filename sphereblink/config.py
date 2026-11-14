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

# The archive's own copy of the same files (IRSA), used when the bucket does
# not answer. It also serves byte ranges, several times slower.
MIRROR_HTTP_BASE = os.environ.get("SPHEREX_MIRROR_HTTP_BASE", "https://irsa.ipac.caltech.edu/ibe/data/spherex")

# Which deployment this is. Anything but "production" (the dev service sets
# "dev") tells search engines to stay away: a test copy must never be found
# in place of the real site. /api/health reports the name.
ENV = os.environ.get("SPHEREX_ENV", "production").strip().lower() or "production"
PRODUCTION = ENV == "production"

CACHE_DIR = Path(os.environ.get("SPHEREX_CACHE_DIR", ".cache/sphereblink"))
DB_URL = os.environ.get("DATABASE_URL", "")  # postgres://... ; empty = SQLite in CACHE_DIR

# Native SPHEREx pixel is 6.15 arcsec.
NATIVE_PIXEL_ARCSEC = 6.15
MAX_FOV_ARCMIN = float(os.environ.get("SPHEREX_MAX_FOV_ARCMIN", "51"))  # 7 x 7 cells of 0.12 deg
# Views wider than this are mosaics: they also use exposures that cover only
# part of the view (see Grid.search_radius_deg).
WIDE_FOV_ARCMIN = float(os.environ.get("SPHEREX_WIDE_FOV_ARCMIN", "12"))
MAX_FRAMES = int(os.environ.get("SPHEREX_MAX_FRAMES", "1200"))
# Fill the overlay catalogues of the featured targets in the background after a start.
WARM_CATALOGS = os.environ.get("SPHEREX_WARM_CATALOGS", "1") not in ("0", "false", "no")
FETCH_WORKERS = int(os.environ.get("SPHEREX_FETCH_WORKERS", "64"))  # concurrent exposures per view
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

# Limits per caller (one IPv4 address or one IPv6 /64). They sit far above
# what a person, or a classroom behind one address, needs, and stop a script.
# 0 turns a rate off.
RATE_REQUESTS_PER_S = float(os.environ.get("SPHEREX_RATE_REQUESTS_PER_S", "40"))  # API calls, sustained
RATE_REQUESTS_BURST = float(os.environ.get("SPHEREX_RATE_REQUESTS_BURST", "1200"))
RATE_NAMES_PER_MIN = float(os.environ.get("SPHEREX_RATE_NAMES_PER_MIN", "40"))  # names sent to the name services
RATE_CATALOGS_PER_MIN = float(os.environ.get("SPHEREX_RATE_CATALOGS_PER_MIN", "60"))  # catalogue queries not yet cached
RATE_MEASURES_PER_HOUR = float(os.environ.get("SPHEREX_RATE_MEASURES_PER_HOUR", "30"))  # new measurements
RATE_MEASURES_BURST = float(os.environ.get("SPHEREX_RATE_MEASURES_BURST", "6"))
MAX_OPEN_REQUESTS = int(os.environ.get("SPHEREX_MAX_OPEN_REQUESTS", "32"))  # API calls being answered at once
# The pipeline works on a limited number of jobs at a time; the next ones
# wait in a queue and their position is reported. The limits follow from what
# the machine has (docs/performance.md, "How many jobs at once"):
# * views whose exposures are being cut: one per worker process
#   (ACTIVE_VIEWS; 0 = no limit), and together no more frames in memory than
#   ACTIVE_MB (a 7 x 7 field holds 0.5-1.2 GB while it is gathered, an
#   8 arcmin view 9 MB; one view is always worked on, whatever its size);
# * measurements: MEASURE_PROCS, each in a process of its own. Two measure as
#   much per minute as three or four, because they share the connections to
#   the bucket.
ACTIVE_VIEWS = int(os.environ.get("SPHEREX_ACTIVE_VIEWS", str(FETCH_PROCS)))
ACTIVE_MB = float(os.environ.get("SPHEREX_ACTIVE_MB", "1200"))
MEASURE_PROCS = max(1, int(os.environ.get("SPHEREX_MEASURE_PROCS", "2")))
# A view that waits in the queue still gets its first few exposures cut, so
# there is a picture on screen while it waits. 0: nothing before its turn.
QUEUE_PREVIEW = os.environ.get("SPHEREX_QUEUE_PREVIEW", "1") not in ("0", "false", "no")
# Views gathered or waiting at once: per caller (more are refused with 429)
# and in total (503). In the queue the callers take turns: the first view of
# every caller comes before anybody's second one.
MAX_JOBS_PER_CLIENT = int(os.environ.get("SPHEREX_MAX_JOBS_PER_CLIENT", "24"))
MAX_JOBS = int(os.environ.get("SPHEREX_MAX_JOBS", "96"))
MAX_FIELDS_PER_CLIENT = int(os.environ.get("SPHEREX_MAX_FIELDS_PER_CLIENT", "8"))  # progressive fields loading at once
MAX_MEASURE_QUEUE = int(os.environ.get("SPHEREX_MAX_MEASURE_QUEUE", "12"))  # measurements waiting or running
# Threads that answer requests. Most of them only wait (for the archive, a
# name service, a view being gathered), so the default of 40 is easy to use up.
REQUEST_THREADS = int(os.environ.get("SPHEREX_REQUEST_THREADS", "160"))
# A resolved object name is kept on disk this long, so a restart does not
# send every name back to the name services.
NAME_TTL_DAYS = float(os.environ.get("SPHEREX_NAME_TTL_DAYS", "30"))
