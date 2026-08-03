#!/usr/bin/env python3
"""§10.3 — what the composition costs in interleaving.

§10.2's six passes per item are not only slower, they are *coarser*.  The
round-robin tie-break of §7.2 applies to sources that complete within the same
event-loop pass, and widening the merge's own cycle widens the window that
counts as "the same pass".  Sources the monolith could tell apart become ties.

Three sources on periods of one, two and three event-loop passes, drained
completely through the monolith and through the composition.  Every item is
delivered by both and each source's own order is untouched by both; what moves
is the order in which the three are interleaved with one another.

That is a real behavioural difference and not a documented one — completion
order, per-source ordering, the tie-break and the backpressure bound all still
hold.  Code asserting a particular cross-source interleaving was relying on
something the merge never promised.
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

PERIODS = {'a': 1, 'b': 2, 'c': 3}
ITEMS = 6

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


def make_sources() -> dict[str, AsyncGenerator[str, None]]:
    def paced(name: str, period: int) -> AsyncGenerator[str, None]:
        async def gen() -> AsyncGenerator[str, None]:
            for index in range(ITEMS):
                for _ in range(period):
                    await asyncio.sleep(0)
                yield f'{name}{index}'

        return gen()

    return {name: paced(name, period) for name, period in PERIODS.items()}


async def drain(merge: Merge) -> list[str]:
    stream = merge(make_sources())
    delivered: list[str] = []
    async with contextlib.aclosing(stream):
        async for _key, task in stream:
            delivered.append(await task)
    return delivered


async def main() -> None:
    monolithic = await drain(monolith)
    composed = await drain(turbopipes.aselect)

    per_source_kept = all(
        [item for item in monolithic if item[0] == name]
        == [item for item in composed if item[0] == name]
        for name in PERIODS
    )

    print(f'monolith   : {" ".join(monolithic)}')
    print(f'composition: {" ".join(composed)}')
    print(f'same items delivered      : {sorted(monolithic) == sorted(composed)}')
    print(f'per-source order unchanged: {per_source_kept}')
    print(f'same interleaving         : {monolithic == composed}')


asyncio.run(main())
