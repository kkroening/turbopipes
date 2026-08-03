#!/usr/bin/env python3
"""§10.2 — what the composition costs in scheduling.

An item travelling through `aselect` now crosses three generator frames rather
than one — `amerge` pulls on `atag`, `atag` pulls on `ataskify`, `ataskify`
pulls on the source — and each of those suspensions is a real event-loop round
trip rather than a function call.

Both merges are run against sources that never await, so nothing but scheduling
is being measured, and the number of event-loop passes between consecutive
deliveries is reported.  With one source that is the per-item cost outright;
with three, the sources are all ready together, so a whole batch is delivered
within one pass and the cost lands on the gap between batches.

The monolith here is §5's merge with §7.2's tie-break, which is what the
library shipped before the decomposition.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Mapping

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

ITEMS = 10

Merge = Callable[
    [Mapping[str, AsyncGenerator[str, None]]],
    AsyncGenerator[tuple[str, asyncio.Task[str]], None],
]


async def monolith(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, asyncio.Task[str]], None]:
    """§5's merge: one function, one generator frame."""
    async with contextlib.AsyncExitStack() as stack:
        for gen in sources.values():
            await stack.enter_async_context(contextlib.aclosing(gen))

        pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
        try:
            while pulls:
                done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
                for task in [task for task in pulls if task in done]:
                    key = pulls.pop(task)
                    if not task.cancelled() and isinstance(
                        task.exception(), StopAsyncIteration
                    ):
                        continue
                    yield key, task
                    pulls[asyncio.create_task(anext(sources[key]))] = key
        finally:
            for task in pulls:
                task.cancel()
            await asyncio.gather(*pulls, return_exceptions=True)


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


def make_sources(count: int) -> dict[str, AsyncGenerator[str, None]]:
    async def never_awaits(name: str) -> AsyncGenerator[str, None]:
        for index in range(ITEMS):
            yield f'{name}{index}'

    return {
        chr(ord('a') + slot): never_awaits(chr(ord('a') + slot))
        for slot in range(count)
    }


async def gaps(merge: Merge, sources: int) -> list[int]:
    ticker = Ticker()
    ticker.start()
    await asyncio.sleep(0)

    stream = merge(make_sources(sources))
    marks: list[int] = []
    async with contextlib.aclosing(stream):
        async for _key, task in stream:
            await task
            marks.append(ticker.count)

    await ticker.stop()
    return [marks[index + 1] - marks[index] for index in range(len(marks) - 1)]


async def run(sources: int) -> None:
    print(f'{sources} source(s), {sources * ITEMS} items delivered:')
    for label, merge in (
        ('monolith   ', monolith),
        ('composition', turbopipes.aselect),
    ):
        measured = await gaps(merge, sources)
        shown = ' '.join(str(gap) for gap in measured[:9])
        print(f'  {label} passes between deliveries: {shown} ...')


async def main() -> None:
    await run(1)
    await run(3)


asyncio.run(main())
