#!/usr/bin/env python3
"""§5.2 — the same scenario as §5, with both teardown phases in place.

Three sources, two of them mid-pull, consumer walks away.  Phase 1 cancels every
in-flight pull and awaits it; phase 2 closes every source.  The ordering is
structural rather than a matter of statement order: the `AsyncExitStack` holding
the source closes is nested *around* the `try`/`finally` that cancels the pulls,
so phase 2 cannot start until phase 1 has finished.

The hand-rolled version and `turbopipes.aselect` are run against the same
scenario, and agree.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Mapping

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

ORDER = ('chatty', 'quiet1', 'quiet2')


def make_sources(finally_ran: list[str]) -> dict[str, AsyncGenerator[str, None]]:
    async def chatty() -> AsyncGenerator[str, None]:
        try:
            index = 0
            while True:
                await asyncio.sleep(0)
                yield f'chatty{index}'
                index += 1
        finally:
            finally_ran.append('chatty')

    async def quiet(name: str) -> AsyncGenerator[str, None]:
        try:
            await asyncio.Event().wait()
            yield f'{name}-unreachable'
        finally:
            finally_ran.append(name)

    return {'chatty': chatty(), 'quiet1': quiet('quiet1'), 'quiet2': quiet('quiet2')}


async def merge(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, str], None]:
    async with contextlib.AsyncExitStack() as stack:  # phase 2, structurally outer
        for gen in sources.values():
            await stack.enter_async_context(contextlib.aclosing(gen))

        pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
        try:
            while pulls:
                done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    key = pulls.pop(task)
                    try:
                        item = task.result()
                    except StopAsyncIteration:
                        continue
                    yield key, item
                    pulls[asyncio.create_task(anext(sources[key]))] = key
        finally:  # phase 1, structurally inner
            for task in pulls:
                task.cancel()
            await asyncio.gather(*pulls, return_exceptions=True)


async def run(label: str, use_aselect: bool) -> None:
    finally_ran: list[str] = []
    sources = make_sources(finally_ran)

    outcome = 'quiet'
    try:
        merged = turbopipes.aselect(sources) if use_aselect else merge(sources)
        async with contextlib.aclosing(merged):
            async for _key, _item in merged:
                break
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        outcome = f'{type(exc).__name__}: {exc}'

    live = [name for name in ORDER if sources[name].ag_frame is not None]
    print(f'{label:<23}teardown: {outcome}')
    print(
        f'  finally ran for: {sorted(finally_ran, key=ORDER.index)} '
        f'| frames live: {live if live else "none"}'
    )


async def main() -> None:
    await run('hand-rolled 2-phase', use_aselect=False)
    await run('turbopipes.aselect', use_aselect=True)


asyncio.run(main())
