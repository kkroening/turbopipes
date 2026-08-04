#!/usr/bin/env python3
"""§7.1 — what a merge does on its own when one source fails.

`amerge` interleaves and nothing else.  It has no opinion about failure, which
means it inherits the one an `async for` over a single generator already has:
the exception ends the iteration.  For a merge that means it ends *every*
source's iteration, however innocent they were.

Three sources, one of which raises on its second pull.  The same scenario is run
twice: straight through `amerge`, and with each source wrapped in `ataskify`
first — which is `aselect`'s arrangement, minus the keys.

Both close all three sources.  The difference is entirely in who gets to decide
what the failure meant.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

TAKE = 8


def make_sources(closed: list[str]) -> dict[str, AsyncGenerator[str, None]]:
    async def good(name: str) -> AsyncGenerator[str, None]:
        try:
            for index in range(100):
                await asyncio.sleep(0)
                yield f'{name}{index}'
        finally:
            closed.append(name)

    async def bad(name: str) -> AsyncGenerator[str, None]:
        try:
            await asyncio.sleep(0)
            yield f'{name}0'
            await asyncio.sleep(0)
            raise ValueError(f'{name} blew up')
        finally:
            closed.append(name)

    return {'good1': good('good1'), 'bad': bad('bad'), 'good2': good('good2')}


async def run_bare() -> None:
    closed: list[str] = []
    sources = make_sources(closed)
    stream = turbopipes.amerge(sources.values())

    got: list[str] = []
    outcome = 'ran to completion'
    try:
        async with contextlib.aclosing(stream):
            async for item in stream:
                got.append(item)
                if len(got) == TAKE:
                    break
    except Exception as exc:  # pylint: disable=broad-exception-caught
        outcome = f'{type(exc).__name__}: {exc}'

    print(f'amerge(sources)               items: {" ".join(got)}')
    print(f'                              consumer saw: {outcome}')
    print(f'                              sources closed: {sorted(closed)}')


async def run_taskified() -> None:
    closed: list[str] = []
    sources = make_sources(closed)
    stream = turbopipes.amerge(
        [turbopipes.ataskify(gen) for gen in sources.values()],
    )

    got: list[str] = []
    failures = 0
    outcome = 'ran to completion'
    try:
        async with contextlib.aclosing(stream):
            async for task in stream:
                try:
                    got.append(await task)
                except Exception:  # pylint: disable=broad-exception-caught
                    failures += 1
                    got.append('<failed>')
                if len(got) == TAKE:
                    break
    except Exception as exc:  # pylint: disable=broad-exception-caught
        outcome = f'{type(exc).__name__}: {exc}'

    print(f'amerge(ataskify(g) for g ...) items: {" ".join(got)}')
    print(
        f'                              consumer saw: {outcome} ({failures} item failed)'
    )
    print(f'                              sources closed: {sorted(closed)}')


async def main() -> None:
    await run_bare()
    await run_taskified()


asyncio.run(main())
