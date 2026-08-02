#!/usr/bin/env python3
"""§4.2 — what three reasonable designs do to the peers of a failing item.

One bad item among three, run through `asyncio.gather`, `asyncio.TaskGroup` and
`turbopipes.aparallel`.

`gather` and `aparallel` print the same row and do not mean the same thing: under
`gather` the peers finish after you have already left via the exception, so their
results are orphaned; under `aparallel` the peers finish *and* every task is
handed to the consumer, the failing one among them.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Awaitable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

PEERS = ('a', 'b')


async def peer(name: str, status: dict[str, str]) -> str:
    try:
        await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        status[name] = f'{name} CANCELLED'
        raise
    status[name] = name
    return name


async def bad() -> str:
    await asyncio.sleep(0.01)
    raise ValueError('bad item')


def report(label: str, status: dict[str, str]) -> None:
    print(f'{label:<23}peers -> {[status.get(name, f"{name} ???") for name in PEERS]}')


async def via_gather() -> None:
    status: dict[str, str] = {}
    peers = [asyncio.ensure_future(peer(name, status)) for name in PEERS]

    with contextlib.suppress(ValueError):
        await asyncio.gather(*peers, bad())

    await asyncio.sleep(0.1)
    await asyncio.gather(*peers, return_exceptions=True)
    report('asyncio.gather', status)


async def via_task_group() -> None:
    status: dict[str, str] = {}

    with contextlib.suppress(BaseExceptionGroup):
        async with asyncio.TaskGroup() as group:
            for name in PEERS:
                group.create_task(peer(name, status))
            group.create_task(bad())

    report('asyncio.TaskGroup', status)


async def via_aparallel() -> None:
    status: dict[str, str] = {}

    async def gen() -> AsyncGenerator[Awaitable[str], None]:
        for name in PEERS:
            yield peer(name, status)
        yield bad()

    pipeline = turbopipes.aparallel(gen(), max_concurrent=3)
    async with contextlib.aclosing(pipeline):
        async for done_task in pipeline:
            # The failure surfaces at the consumer's own `await`, and the
            # consumer decides what it means.  Here: carry on.
            with contextlib.suppress(ValueError):
                await done_task

    report('turbopipes.aparallel', status)


async def main() -> None:
    await via_gather()
    await via_task_group()
    await via_aparallel()


asyncio.run(main())
