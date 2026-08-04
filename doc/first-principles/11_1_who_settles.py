#!/usr/bin/env python3
"""§11.1 — every layer that arms a pull has to settle its own.

The worry about splitting §5's teardown across three functions was that the
discipline would have to be reimplemented in each of them.  It does — and that
is the right answer rather than a failure, because the pulls are genuinely
different pulls.  `amerge` holds one per `atag` generator; `ataskify` holds one
per *source*; `atag` holds none at all and needs no settling.

Removing either settling is a falsification, and the two fail differently.  The
scenario is §5's: three sources, two of them suspended inside their own body
mid-pull, and a consumer that takes one item and leaves.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Iterable
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

ORDER = ('chatty', 'quiet1', 'quiet2')

Merge = Callable[
    [Iterable[AsyncGenerator[tuple[str, asyncio.Task[str]], None]]],
    AsyncGenerator[tuple[str, asyncio.Task[str]], None],
]
Taskify = Callable[
    [AsyncGenerator[str, None]],
    AsyncGenerator[asyncio.Task[str], None],
]


async def ataskify_unsettled(
    gen: AsyncGenerator[str, None],
) -> AsyncGenerator[asyncio.Task[str], None]:
    """`ataskify` with its `asettle` removed; `amerge`'s is left intact."""
    async with contextlib.aclosing(gen):
        while True:
            task = asyncio.create_task(anext(gen))
            await asyncio.wait([task])
            if not task.cancelled() and isinstance(
                task.exception(), StopAsyncIteration
            ):
                break
            yield task


async def amerge_unsettled(
    gens: Iterable[AsyncGenerator[tuple[str, asyncio.Task[str]], None]],
) -> AsyncGenerator[tuple[str, asyncio.Task[str]], None]:
    """`amerge` with its `asettle` removed; `ataskify`'s is left intact."""
    gens = [*gens]
    pulls = {}
    async with turbopipes.aclosing_all(gens):
        for gen in gens:
            pulls[asyncio.create_task(anext(gen))] = gen
        while pulls:
            done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
            for task in [task for task in pulls if task in done]:
                gen = pulls.pop(task)
                if not task.cancelled() and isinstance(
                    task.exception(), StopAsyncIteration
                ):
                    continue
                yield task.result()
                pulls[asyncio.create_task(anext(gen))] = gen


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


async def cancel_leftovers() -> None:
    """Clears out whatever a mutant left running, so the next run starts clean."""
    current = asyncio.current_task()
    leftover = [task for task in asyncio.all_tasks() if task is not current]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


async def run(label: str, merge: Merge, taskify: Taskify) -> None:
    reported: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))

    closed: list[str] = []
    sources = make_sources(closed)
    outcome = 'quiet'
    try:
        stream = merge(
            [turbopipes.atag(key, taskify(gen)) for key, gen in sources.items()],
        )
        async with contextlib.aclosing(stream):
            async for _key, _task in stream:
                break
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        outcome = f'{type(exc).__name__}: {exc}'
    finally:
        loop.set_exception_handler(previous)

    live = [name for name in ORDER if sources[name].ag_frame is not None]
    print(f'{label}  consumer saw: {outcome}')
    print(
        f'{" " * len(label)}  closed: {sorted(closed, key=ORDER.index) or "none"} '
        f'| frames live: {live or "none"} | sent to the loop: {len(reported)}'
    )

    await cancel_leftovers()


async def main() -> None:
    await run('both settle       ', turbopipes.amerge, turbopipes.ataskify)
    await run("ataskify doesn't  ", turbopipes.amerge, ataskify_unsettled)
    await run("amerge doesn't    ", amerge_unsettled, turbopipes.ataskify)


asyncio.run(main())
