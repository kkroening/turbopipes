#!/usr/bin/env python3
"""§8.1 — why `ataskify` waits on the pull instead of awaiting it.

The two spellings look interchangeable.  They are not: awaiting the pull
re-raises whatever the source raised *inside `ataskify`'s own body*, so the
failure becomes this generator's failure and ends the iteration — which is
exactly the coupling `ataskify` exists to remove.

`asyncio.wait([task])` returns when the task is done and raises nothing.  The
outcome stays sealed inside the task and is handed to the consumer intact.

A source that yields two rows and then raises, consumed through each variant.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

Taskify = Callable[
    [AsyncGenerator[str, None]],
    AsyncGenerator[asyncio.Task[str], None],
]


async def taskify_await(
    gen: AsyncGenerator[str, None],
) -> AsyncGenerator[asyncio.Task[str], None]:
    """The tempting variant: `await` the pull rather than waiting on it."""
    async with contextlib.aclosing(gen):
        while True:
            task = asyncio.create_task(anext(gen))
            try:
                await task  # <- the whole difference
            except StopAsyncIteration:
                break
            yield task


async def source(closed: list[str]) -> AsyncGenerator[str, None]:
    try:
        yield 'row-1'
        yield 'row-2'
        raise ValueError('row 3 is malformed')
    finally:
        closed.append('closed')


async def run(label: str, taskify: Taskify) -> None:
    closed: list[str] = []
    stream = taskify(source(closed))

    got: list[str] = []
    outcome = 'iteration ended normally'
    try:
        async with contextlib.aclosing(stream):
            async for task in stream:
                try:
                    got.append(await task)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    got.append(f'<{type(exc).__name__}: {exc}>')
    except Exception as exc:  # pylint: disable=broad-exception-caught
        outcome = f'{type(exc).__name__}: {exc}'

    print(f'{label}  {got}')
    print(f'{" " * len(label)}  {outcome} | source closed: {closed == ["closed"]}')


async def main() -> None:
    await run('await the pull  ', taskify_await)
    await run('wait on the pull', turbopipes.ataskify)


asyncio.run(main())
