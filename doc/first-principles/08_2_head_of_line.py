#!/usr/bin/env python3
"""§8.2 — why `ataskify` holds the pull until it completes.

Yielding the pull the instant it is armed looks strictly better: no waiting, the
consumer gets its task sooner, and the task carries the wait anyway.  It isn't.
A generator that yields an *unfinished* pull is parked at its `yield`, so a
merge above it finds it ready immediately — and finds every one of its peers
ready immediately too, whatever their sources are actually doing.

Readiness ordering is then gone.  The merge hands out tasks in arming order
rather than completion order, and the consumer's own `await` becomes the place
the waiting happens — one item at a time, in whatever order it was handed them.
A fast source's ready item sits behind a slow source's unfinished one: classic
head-of-line blocking, reintroduced one layer up from where §2 removed it.

Two sources — one instant, one taking ten event-loop passes per item — merged
both ways.  `item@N` reads "delivered to the consumer N event-loop passes in".
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

SLOW_PASSES = 10
ITEMS = 4

Taskify = Callable[
    [AsyncGenerator[str, None]],
    AsyncGenerator[asyncio.Task[str], None],
]


async def taskify_eager(
    gen: AsyncGenerator[str, None],
) -> AsyncGenerator[asyncio.Task[str], None]:
    """Yields the pull *before* it completes."""
    armed: list[asyncio.Task[str]] = []
    async with contextlib.aclosing(gen):
        try:
            while True:
                task = asyncio.create_task(anext(gen))
                armed.append(task)
                yield task  # <- no wait
                armed.clear()
                if task.cancelled() or isinstance(task.exception(), StopAsyncIteration):
                    break
        finally:
            await turbopipes.asettle(armed)


class Ticker:
    """Counts event-loop passes, one per `asyncio.sleep(0)` round trip."""

    def __init__(self) -> None:
        self.count = 0
        self.task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(0)
            self.count += 1

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        assert self.task is not None
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


def make_sources() -> dict[str, AsyncGenerator[str, None]]:
    async def instant(name: str) -> AsyncGenerator[str, None]:
        for index in range(ITEMS):
            yield f'{name}{index}'

    async def slow(name: str) -> AsyncGenerator[str, None]:
        for index in range(ITEMS):
            for _ in range(SLOW_PASSES):
                await asyncio.sleep(0)
            yield f'{name}{index}'

    return {'slow': slow('slow'), 'fast': instant('fast')}


async def run(label: str, taskify: Taskify) -> None:
    ticker = Ticker()
    ticker.start()
    await asyncio.sleep(0)

    sources = make_sources()
    stream = turbopipes.amerge(
        [turbopipes.atag(key, taskify(gen)) for key, gen in sources.items()],
    )

    base = ticker.count
    delivered: list[str] = []
    async with contextlib.aclosing(stream):
        async for _key, task in stream:
            delivered.append(f'{await task}@{ticker.count - base}')
            if len(delivered) == 2 * ITEMS:
                break

    await ticker.stop()
    print(f'{label}  {" ".join(delivered)}')


async def main() -> None:
    await run('ataskify, waiting  ', turbopipes.ataskify)
    await run('yielding unawaited ', taskify_eager)


asyncio.run(main())
