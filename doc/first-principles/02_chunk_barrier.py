#!/usr/bin/env python3
"""§2 — the chunk boundary is a synchronization barrier nobody asked for.

Twenty-four items, chunks of eight, three of them slow (one per chunk).  Prints
wall-clock timings and the mean number of tasks actually in flight, sampled
every 5 ms.

This is the one measurement in the document that is a wall-clock benchmark, so
its exact digits move a little between machines and runs.  The shape does not:
the chunked version's window is nominally eight wide and effectively one.
"""

import asyncio
import contextlib
import os
import statistics
import sys
import time
from collections.abc import AsyncGenerator
from collections.abc import Awaitable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

ITEMS = 24
CHUNK = 8
SLOW = {0, 8, 16}  # one straggler per chunk
SLOW_SECS = 1.0
FAST_SECS = 0.01
SAMPLE_SECS = 0.005

_in_flight = 0


async def work(index: int) -> int:
    global _in_flight  # pylint: disable=global-statement
    _in_flight += 1
    try:
        await asyncio.sleep(SLOW_SECS if index in SLOW else FAST_SECS)
    finally:
        _in_flight -= 1
    return index


async def sample(into: list[int]) -> None:
    while True:
        await asyncio.sleep(SAMPLE_SECS)
        into.append(_in_flight)


async def chunked() -> AsyncGenerator[int, None]:
    for start in range(0, ITEMS, CHUNK):
        chunk = range(start, min(start + CHUNK, ITEMS))
        for result in await asyncio.gather(*(work(index) for index in chunk)):
            yield result


async def parallel() -> AsyncGenerator[int, None]:
    async def gen() -> AsyncGenerator[Awaitable[int], None]:
        for index in range(ITEMS):
            yield work(index)

    pipeline = turbopipes.aparallel(gen(), max_concurrent=CHUNK)
    async with contextlib.aclosing(pipeline):
        async for done_task in pipeline:
            yield await done_task


async def measure(label: str, stream: AsyncGenerator[int, None]) -> None:
    samples: list[int] = []
    sampler = asyncio.create_task(sample(samples))
    started = time.monotonic()
    first = None
    async for _ in stream:
        if first is None:
            first = time.monotonic() - started
    total = time.monotonic() - started
    sampler.cancel()
    await asyncio.gather(sampler, return_exceptions=True)
    mean = statistics.mean(samples) if samples else 0.0
    print(
        f'{label:<31}first result {first:.2f}s | total {total:.2f}s '
        f'| mean tasks in flight {mean:.1f} of {CHUNK}'
    )


async def main() -> None:
    await measure(f'gather in chunks of {CHUNK}', chunked())
    await measure(f'aparallel(max_concurrent={CHUNK})', parallel())


asyncio.run(main())
