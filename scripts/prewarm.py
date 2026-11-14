"""Warm the cache of a running SPHEREx Blink server.

    python scripts/prewarm.py https://your-server [--fov 4,8,15] [coords.txt]

Opens every featured target (and every "ra dec" line of the optional file) at
each field of view, waits until the server has read all exposures, and prints
how long it took. Run it after a deploy or when a new data release appears.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=120) as r:
        return json.loads(r.read())


def warm(base: str, query: dict) -> str:
    q = urllib.parse.urlencode(query)
    t0 = time.time()
    while True:
        job = get(base, "/api/view?" + q)["job"]
        if job["status"] in ("ready", "error"):
            break
        time.sleep(0.5)
    took = time.time() - t0
    return f"{q}: {job['status']} exposures={job['total']} frames={job['frames']} failed={job['failed']} in {took:.1f}s {job['error']}"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    base, fovs, views = argv[0].rstrip("/"), [8.0], []
    rest = argv[1:]
    while rest:
        a = rest.pop(0)
        if a == "--fov":
            fovs = [float(x) for x in rest.pop(0).split(",")]
        else:
            for line in open(a, encoding="utf-8"):
                parts = line.replace(",", " ").split()
                if len(parts) >= 2 and not line.lstrip().startswith("#"):
                    views.append({"ra": float(parts[0]), "dec": float(parts[1])})
    views = [{"target": f["key"]} for f in get(base, "/api/featured")] + views
    for fov in fovs:
        for v in views:
            print(warm(base, {**v, "fov": fov}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
