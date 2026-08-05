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

Arrival is reported as a whole number of quiet-source periods rather than in
milliseconds, because the period is what the finding is about and a period is
wide enough that scheduling slop cannot move an item between two of them.  A
millisecond figure here would be sampled from the run; a period count is fixed
by the design.
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from collections.abc import Mapping

QUIET_PERIOD = 0.05
ITEMS = 4


def _period(elapsed: float) -> int:
    """Which quiet-source period this arrival lands in.

    A period is `QUIET_PERIOD` wide, so the few milliseconds of scheduling slop
    that accumulate across a run cannot carry an arrival into the next one.
    """
    return int(round(elapsed / QUIET_PERIOD))


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
        arrivals.append(f'{item}@{_period(time.monotonic() - started)}')
    print(f'  {label:<18} {" ".join(arrivals)}')


async def main() -> None:
    period_ms = int(QUIET_PERIOD * 1000)
    print(
        f'two sources, quiet one speaks every {period_ms}ms; '
        f'@N = arrived in the Nth {period_ms}ms period:'
    )
    await _drain('round-robin', _round_robin(_sources()))
    await _drain('first-finished', _first_finished(_sources()))


if __name__ == '__main__':
    asyncio.run(main())
