#!/usr/bin/env python3
"""§11.2 — the two primitives nest one way round only.

`aclosing_all` closes the sources; `asettle` cancels the pulls that make them
closeable.  Both have to run, and §5.2 established the order: settle first.
Getting the order right is not a matter of writing the two statements in the
right sequence, because both run from cleanup paths — it is a matter of which
one is nested inside the other.

    async with aclosing_all(gens):        # closes, structurally OUTER
        try:
            ...
        finally:
            await asettle(pulls)          # settles, structurally INNER

Turned inside out, the `async with` unwinds first and closes sources that are
still mid-pull, which is §5.1's `RuntimeError` arriving from the cleanup path.
Both arrangements are run against §5's scenario — three sources, two suspended
inside their own body — left by `break` and by `raise`.

The reversed arrangement does eventually tear everything down, because its
later `asettle` still cancels the pulls.  What it loses is the consumer's own
exception, replaced on the way out by a complaint about generator state.
"""

import asyncio
import contextlib
import os
import sys
import traceback
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Iterable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

ORDER = ('chatty', 'quiet1', 'quiet2')
MESSAGE = 'consumer said no'

Merge = Callable[
    [Iterable[AsyncGenerator[str, None]]],
    AsyncGenerator[str, None],
]


async def merge_settle_inside(
    gens: Iterable[AsyncGenerator[str, None]],
) -> AsyncGenerator[str, None]:
    gens = [*gens]
    pulls: dict[asyncio.Task[str], AsyncGenerator[str, None]] = {}
    async with turbopipes.aclosing_all(gens):
        try:
            for gen in gens:
                pulls[asyncio.create_task(anext(gen))] = gen
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
        finally:
            await turbopipes.asettle([*pulls])


async def merge_settle_outside(
    gens: Iterable[AsyncGenerator[str, None]],
) -> AsyncGenerator[str, None]:
    gens = [*gens]
    pulls: dict[asyncio.Task[str], AsyncGenerator[str, None]] = {}
    try:
        async with turbopipes.aclosing_all(gens):
            for gen in gens:
                pulls[asyncio.create_task(anext(gen))] = gen
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
    finally:
        await turbopipes.asettle([*pulls])


def make_sources(closed: list[str]) -> dict[str, AsyncGenerator[str, None]]:
    async def chatty() -> AsyncGenerator[str, None]:
        try:
            index = 0
            while True:
                await asyncio.sleep(0)
                yield f'chatty{index}'
                index += 1
        finally:
            closed.append('chatty')

    async def quiet(name: str) -> AsyncGenerator[str, None]:
        try:
            await asyncio.Event().wait()  # suspended inside its own body
            yield f'{name}-unreachable'
        finally:
            closed.append(name)

    return {'chatty': chatty(), 'quiet1': quiet('quiet1'), 'quiet2': quiet('quiet2')}


def mentions_the_consumers_exception(exc: BaseException) -> bool:
    """Searches `__context__`, `__cause__`, and the rendered traceback."""
    found = MESSAGE in ''.join(traceback.format_exception(exc))
    pending: list[BaseException | None] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is not None and id(current) not in seen:
            seen.add(id(current))
            found = found or isinstance(current, ValueError)
            pending.append(current.__context__)
            pending.append(current.__cause__)
    return found


async def run(how: str, label: str, merge: Merge) -> BaseException | None:
    closed: list[str] = []
    sources = make_sources(closed)
    escaped: BaseException | None = None
    try:
        stream = merge(sources.values())
        async with contextlib.aclosing(stream):
            async for _item in stream:
                if how == 'raise':
                    raise ValueError(MESSAGE)
                break
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        escaped = exc

    live = [name for name in ORDER if sources[name].ag_frame is not None]
    summary = (
        'nothing raised' if escaped is None else f'{type(escaped).__name__}: {escaped}'
    )
    print(f'{how + ", " + label:<32}-> {summary}')
    print(
        f'{"":<32}   closed: {sorted(closed, key=ORDER.index)} '
        f'| frames live: {live or "none"}'
    )

    current = asyncio.current_task()
    leftover = [task for task in asyncio.all_tasks() if task is not current]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)
    return escaped


async def main() -> None:
    for how in ('break', 'raise'):
        await run(how, 'settle inside the closes', merge_settle_inside)
        escaped = await run(how, 'settle outside them', merge_settle_outside)
        if how == 'raise':
            survived = escaped is not None and mentions_the_consumers_exception(escaped)
            print(f"  consumer's ValueError anywhere in the reversed one? {survived}")


asyncio.run(main())
