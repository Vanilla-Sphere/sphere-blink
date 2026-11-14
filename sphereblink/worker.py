"""Worker processes for cutting exposures.

Reading a cutout is a few network waits plus ~40 ms of CPU (Rice decoding,
WCS, resampling). One Python process can only use one core for that, so on
a multi-core server the exposures of a view are split into chunks that are
cut in several processes, each with its own event loop and HTTP connections.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import numpy as np

from .cutout import NET, Frame, Grid, cut_async
from .sia import Exposure

_loop: asyncio.AbstractEventLoop | None = None
_client: httpx.AsyncClient | None = None

Item = tuple[str, Exposure, "np.ndarray | None"]  # cache key, exposure, wavelength map
Result = tuple[str, Exposure, "Frame | None", str]  # ..., frame (None = misses the grid), error text


def init(connections: int) -> None:
    global _loop, _client
    _loop = asyncio.new_event_loop()
    limits = httpx.Limits(max_connections=connections * 2, max_keepalive_connections=connections * 2)
    _client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20), limits=limits)


async def cut_one(client: httpx.AsyncClient, key: str, e: Exposure, grid: Grid, wmap) -> Result:
    last = ""
    for attempt in range(3):
        try:
            return key, e, await cut_async(client, e, grid, wmap), ""
        except Exception as exc:  # network hiccup or an unexpected file layout: retry, then report
            last = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(1.0 * (attempt + 1))
    return key, e, None, last or "failed"


class Meter:
    """Measures what a batch of cutouts cost: archive traffic and CPU."""

    def __enter__(self):
        self._net, self._cpu, self._wall = dict(NET), time.process_time(), time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.stats = {
            "requests": NET["requests"] - self._net["requests"],
            "mb": (NET["bytes"] - self._net["bytes"]) / 1e6,
            "net_s": NET["seconds"] - self._net["seconds"],  # summed over concurrent requests
            "cpu_s": time.process_time() - self._cpu,
            "busy_s": time.perf_counter() - self._wall,  # summed over worker processes
        }


def cut_chunk(grid: Grid, items: list[Item]) -> tuple[list[Result], dict]:
    """Cut a chunk of exposures concurrently (runs inside a worker process)."""
    assert _loop is not None and _client is not None, "worker not initialised"

    async def run() -> list[Result]:
        return list(await asyncio.gather(*(cut_one(_client, k, e, grid, w) for k, e, w in items)))

    with Meter() as m:
        results = _loop.run_until_complete(run())
    return results, m.stats
