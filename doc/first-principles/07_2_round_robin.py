#!/usr/bin/env python3
"""§7.2 — the tie-break the obvious merge doesn't have.

`asyncio.wait` returns its completed set as a `set`, and iterating that set is
iterating in hash order — which for `Task` objects is address order, and so
arbitrary.  Every source ready in the same pass is therefore served in an order
that has nothing to do with when it was armed.

`amerge` filters `pulls` by `done` instead of iterating `done`.  `pulls` is
keyed in arming order and a source isn't re-armed until it's been served, so
serving one sends it to the back: least-recently-served first.

Eight sources that are ready every pass, sixty-four items taken, twenty trials
each.  Two things are measured: the worst gap between consecutive services of
any one source, and whether the twenty trials all produced the same
interleaving.  Not how many distinct ones they produced — that count is set
order over `Task` objects, so it is address order and moves with allocation
history, which makes it the wrong thing to quote.

Round-robin's worst gap is `n` by construction.  Arbitrary order's worst case is
`2n - 1` — served first in one pass, last in the next — and twenty trials find
it every time.

What is reported is the worst gap across the twenty trials, and only that.  An
individual trial can come in under `2n - 1`, and whether it does is a property of
the twenty draws rather than of the merge, so the spread beneath the ceiling is
a sampled quantity and is deliberately not reported.  The ceiling is the claim,
and it holds at every source count.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Iterable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

SOURCES = 8
ITEMS = 16
TAKE = 64
TRIALS = 20

Merge = Callable[
    [Iterable[AsyncGenerator[str, None]]],
    AsyncGenerator[str, None],
]


async def merge_set_order(
    gens: Iterable[AsyncGenerator[str, None]],
) -> AsyncGenerator[str, None]:
    """The obvious merge: iterate `done` directly."""
    pulls = {asyncio.create_task(anext(gen)): gen for gen in gens}
    while pulls:
        done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            gen = pulls.pop(task)
            try:
                item = task.result()
            except StopAsyncIteration:
                continue
            yield item
            pulls[asyncio.create_task(anext(gen))] = gen


async def merge_round_robin(
    gens: Iterable[AsyncGenerator[str, None]],
) -> AsyncGenerator[str, None]:
    """`amerge`'s tie-break: filter `pulls` by `done`, keeping arming order."""
    pulls = {asyncio.create_task(anext(gen)): gen for gen in gens}
    while pulls:
        done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
        for task in [task for task in pulls if task in done]:
            gen = pulls.pop(task)
            try:
                item = task.result()
            except StopAsyncIteration:
                continue
            yield item
            pulls[asyncio.create_task(anext(gen))] = gen


def make_sources() -> list[AsyncGenerator[str, None]]:
    async def ready_every_pass(name: str) -> AsyncGenerator[str, None]:
        for index in range(ITEMS):
            yield f'{name}{index}'

    return [ready_every_pass(chr(ord('a') + slot)) for slot in range(SOURCES)]


async def collect(merge: Merge) -> list[str]:
    stream = merge(make_sources())
    items: list[str] = []
    async with contextlib.aclosing(stream):
        async for item in stream:
            items.append(item)
            if len(items) == TAKE:
                break
    return items


def worst_service_gap(items: list[str]) -> int:
    last: dict[str, int] = {}
    worst = 0
    for position, item in enumerate(items):
        name = item[0]
        if name in last:
            worst = max(worst, position - last[name])
        last[name] = position
    return worst


async def run(label: str, merge: Merge) -> None:
    trials = [await collect(merge) for _ in range(TRIALS)]
    worst = max(worst_service_gap(items) for items in trials)
    identical = 'yes' if len({tuple(items) for items in trials}) == 1 else 'no'
    print(
        f'{label}  worst service gap: {worst:<5} '
        f'| all {TRIALS} trials identical: {identical}'
    )


async def main() -> None:
    await run('iterate `done`, a set ', merge_set_order)
    await run('filter `pulls` by it  ', merge_round_robin)
    await run('turbopipes.amerge     ', turbopipes.amerge)


asyncio.run(main())
