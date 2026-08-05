#!/usr/bin/env python3
"""§5 — why asking each source in turn is not a merge.

Two sources with different rhythms, which is the normal case rather than a
contrived one: `chatty` has an item ready almost immediately and keeps having
them, `quiet` speaks once every 50 ms.  A round-robin loop pulls from each in
turn, so a pull that hasn't finished is a pull the whole loop is parked on.

The delivery order is the finding.  Round-robin alternates strictly — one
chatty, one quiet, one chatty — because it cannot take a second item from
`chatty` until `quiet` has produced.  Every chatty item after the first is
therefore delivered at `quiet`'s pace, not its own, and the arrival timings show
it: the whole stream advances in 50 ms steps.

Arming both at once and taking whichever finishes first delivers each item at
roughly the moment it became available, which is what the merge has to do.

Timings are rounded to the nearest 5 ms so the output is stable enough to paste
into the guide; the point is the shape of the ladder, not the exact figures.
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from collections.abc import Mapping

QUIET_PERIOD = 0.05
ITEMS = 4


def _quantize(elapsed: float) -> int:
    """Round to the nearest 5 ms, reported in whole milliseconds."""
    return int(round(elapsed * 1000 / 5.0) * 5)


async def _chatty() -> AsyncGenerator[str, None]:
    """Always has something to say; never makes the consumer wait."""
    for i in range(ITEMS):
        yield f'chatty{i}'


async def _quiet(period: float) -> AsyncGenerator[str, None]:
    """Speaks once per `period`, which is the rhythm the merge must not impose
    on anybody else."""
    for i in range(ITEMS):
        await asyncio.sleep(period)
        yield f'quiet{i}'


def _sources() -> dict[str, AsyncGenerator[str, None]]:
    return {'chatty': _chatty(), 'quiet': _quiet(QUIET_PERIOD)}


async def _round_robin(
    sources: dict[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, str], None]:
    """Ask each source in turn, in order, one pull at a time."""
    remaining = dict(sources)
    while remaining:
        for key in list(remaining):
            try:
                yield key, await anext(remaining[key])
            except StopAsyncIteration:
                del remaining[key]


async def _first_finished(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, str], None]:
    """Arm one pull per source; yield whichever finishes first; re-arm it."""
    pulls = {
        asyncio.ensure_future(anext(gen)): key for key, gen in sources.items()
    }
    while pulls:
        done, _ = await asyncio.wait(
            pulls, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            key = pulls.pop(task)
            try:
                item = task.result()
            except StopAsyncIteration:
                continue
            yield key, item
            pulls[asyncio.ensure_future(anext(sources[key]))] = key


async def _drain(
    label: str,
    merge: AsyncGenerator[tuple[str, str], None],
) -> None:
    started = time.monotonic()
    arrivals = []
    async for _key, item in merge:
        arrivals.append(f'{item}@{_quantize(time.monotonic() - started)}ms')
    print(f'  {label:<18} {" ".join(arrivals)}')


async def main() -> None:
    print(f'two sources, quiet one speaks every {int(QUIET_PERIOD * 1000)}ms:')
    await _drain('round-robin', _round_robin(_sources()))
    await _drain('first-finished', _first_finished(_sources()))


if __name__ == '__main__':
    asyncio.run(main())
