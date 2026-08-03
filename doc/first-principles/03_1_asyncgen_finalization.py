#!/usr/bin/env python3
"""§3.1 — async generator cleanup is not the forgiving thing you're used to.

Same loop, same `break`, one keyword different.  The sync generator's `finally`
runs at the `break`, courtesy of refcounting.  The async generator's does not:
closing it means *awaiting* it, and `__del__` cannot await, so asyncio schedules
the close for a later turn of the event loop.
"""

import asyncio
import gc
from collections.abc import AsyncGenerator
from collections.abc import Generator

PASSES = 50

log: list[str] = []


def sync_source() -> Generator[int, None, None]:
    try:
        for index in range(1000):
            yield index
    finally:
        log.append('sync finally')


async def async_source() -> AsyncGenerator[int, None]:
    try:
        for index in range(1000):
            yield index
    finally:
        log.append('async finally')


async def main() -> None:
    log.clear()
    for item in sync_source():
        if item == 2:
            break
    gc.collect()
    print(f'sync : right after the loop -> {log}')

    log.clear()
    async for item in async_source():
        if item == 2:
            break
    gc.collect()
    print(f'async: right after the loop -> {log}')

    for _ in range(PASSES):
        await asyncio.sleep(0)
    print(f'async: {PASSES} loop passes later -> {log}')


asyncio.run(main())
