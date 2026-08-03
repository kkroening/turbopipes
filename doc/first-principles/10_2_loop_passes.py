#!/usr/bin/env python3
"""§10.2 — what the composition costs in scheduling.

An item travelling through `aselect` now crosses three generator frames rather
than one — `amerge` pulls on `atag`, `atag` pulls on `ataskify`, `ataskify`
pulls on the source — but the frames are not what costs.  Crossing one is an
`await` on a coroutine, and an `await` only reaches the event loop if something
along it actually suspends.  What suspends is a layer that hands its pull to a
task and then waits on the task, and the composition has two of those where the
monolith had one.

Both merges are run against sources that never await, so nothing but scheduling
is being measured, and the number of event-loop passes between consecutive
deliveries is reported.  With one source that is the per-item cost outright;
with three, the sources are all ready together, so a whole batch is delivered
within one pass and the cost lands on the gap between batches.

A ladder of arrangements then varies the two counts independently, so that the
doubling can be attributed rather than assumed: the first four hold the waiting
layers at one and take the frames from one to three, and the last four add a
second and a third waiting layer.  Of those, the final two stack `ataskify` on
itself, which nobody would write; they are there so that the rule is checked
somewhere other than the two points the doubling itself provides.

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

Sources = Mapping[str, AsyncGenerator[str, None]]
Merge = Callable[[Sources], AsyncGenerator[object, None]]


async def monolith(
    sources: Sources,
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


async def consume(item: object) -> None:
    """Awaits every task the arrangement delivered, whatever shape it arrives in.

    The ladder's arrangements deliver different shapes - a bare value, a `(key,
    value)` pair, a task, a tagged task, and a tagged task nested inside another
    one - so the walk is exhaustive through both tuples and tasks rather than
    stopping at the first value it can't unwrap.  Its own await count therefore
    tracks how many tasks an arrangement delivers: none, ten or twenty across
    the ladder.

    That this costs nothing is measured rather than granted.  The monolith
    delivers ten tasks and `amerge(src)` delivers none, and both cost three
    passes - ten awaits of difference for zero passes of difference.  Awaiting a
    task that has already completed never reaches the loop.
    """
    pending: list[object] = [item]
    while pending:
        value = pending.pop()
        if isinstance(value, asyncio.Task):
            pending.append(await value)
        elif isinstance(value, tuple):
            pending.extend(value)


async def gaps(merge: Merge, sources: int) -> list[int]:
    ticker = Ticker()
    ticker.start()
    await asyncio.sleep(0)

    stream = merge(make_sources(sources))
    marks: list[int] = []
    async with contextlib.aclosing(stream):
        async for item in stream:
            await consume(item)
            marks.append(ticker.count)

    await ticker.stop()
    return [marks[index + 1] - marks[index] for index in range(len(marks) - 1)]


def bare(sources: Sources) -> AsyncGenerator[str, None]:
    """One frame, one waiting layer: the merge over the sources themselves."""
    return turbopipes.amerge(list(sources.values()))


def tagged(sources: Sources) -> AsyncGenerator[tuple[str, str], None]:
    """Two frames, one waiting layer."""
    return turbopipes.amerge(
        [turbopipes.atag(key, gen) for key, gen in sources.items()]
    )


def tagged_twice(sources: Sources) -> AsyncGenerator[tuple[str, tuple[str, str]], None]:
    """Three frames - `aselect`'s own count - still with one waiting layer."""
    return turbopipes.amerge(
        [
            turbopipes.atag(key, turbopipes.atag(key, gen))
            for key, gen in sources.items()
        ]
    )


def taskified(sources: Sources) -> AsyncGenerator[asyncio.Task[str], None]:
    """Two frames, two waiting layers: the second one is `ataskify`'s."""
    return turbopipes.amerge(
        [turbopipes.ataskify(gen, label=key) for key, gen in sources.items()]
    )


def composition(
    sources: Sources,
) -> AsyncGenerator[tuple[str, asyncio.Task[str]], None]:
    """Three frames, two waiting layers - `aselect`, written out."""
    return turbopipes.amerge(
        [
            turbopipes.atag(key, turbopipes.ataskify(gen, label=key))
            for key, gen in sources.items()
        ]
    )


def taskified_twice(
    sources: Sources,
) -> AsyncGenerator[asyncio.Task[asyncio.Task[str]], None]:
    """Three frames, three waiting layers.  Nobody would write this."""
    return turbopipes.amerge(
        [
            turbopipes.ataskify(turbopipes.ataskify(gen, label=key), label=key)
            for key, gen in sources.items()
        ]
    )


def stacked(sources: Sources) -> AsyncGenerator[object, None]:
    """Five frames, three waiting layers.  Nor this - it is the frame control."""
    return turbopipes.amerge(
        [
            turbopipes.atag(
                key,
                turbopipes.ataskify(
                    turbopipes.atag(key, turbopipes.ataskify(gen, label=key)),
                    label=key,
                ),
            )
            for key, gen in sources.items()
        ]
    )


LADDER: tuple[tuple[str, int, int, Merge], ...] = (
    ('monolith', 1, 1, monolith),
    ('amerge(src)', 1, 1, bare),
    ('amerge(atag(src))', 2, 1, tagged),
    ('amerge(atag(atag(src)))', 3, 1, tagged_twice),
    ('amerge(ataskify(src))', 2, 2, taskified),
    ('amerge(atag(ataskify(src)))', 3, 2, composition),
    ('amerge(ataskify(ataskify(src)))', 3, 3, taskified_twice),
    ('amerge(atag(ataskify(atag(ataskify(src)))))', 5, 3, stacked),
)


async def run(sources: int) -> None:
    print(f'{sources} source(s), {sources * ITEMS} items delivered:')
    for label, merge in (
        ('monolith   ', monolith),
        ('composition', turbopipes.aselect),
    ):
        measured = await gaps(merge, sources)
        shown = ' '.join(str(gap) for gap in measured[:9])
        print(f'  {label} passes between deliveries: {shown} ...')


async def run_ladder() -> None:
    width = max(len(label) for label, _, _, _ in LADDER)
    heading = 'arrangement'
    print('one source, frames and waiting layers varied independently:')
    print(f'  {heading:{width}s}  frames  waits  passes')
    for label, frames, waits, merge in LADDER:
        measured = await gaps(merge, 1)
        shown = ' '.join(str(gap) for gap in measured[:5])
        print(f'  {label:{width}s}  {frames:6d}  {waits:5d}  {shown} ...')


async def main() -> None:
    await run(1)
    await run(3)
    await run_ladder()


asyncio.run(main())
