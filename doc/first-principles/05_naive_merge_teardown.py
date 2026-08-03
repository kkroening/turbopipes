#!/usr/bin/env python3
"""§5 — the naive merge, closed the way §3.1 taught you, with sources mid-pull.

`aclosing` over every source is not sufficient by itself here: two of the three
sources are suspended at an `await` inside their own bodies when consumption
stops, and `aclose()` refuses that state.  The arrangement isn't wrong so much as
incomplete — something has to reach a mid-pull source before it can be closed at
all, which is what §5.2 adds in front of it.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import Mapping

finally_ran: list[str] = []


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
        await asyncio.Event().wait()  # suspended inside the body; nothing ever sets it
        yield f'{name}-unreachable'
    finally:
        finally_ran.append(name)


async def merge(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, str], None]:
    """The merge owns its sources, the way `aselect` does.

    The `AsyncExitStack` of source closes wraps the merge loop *inside* the
    generator, so closing the merge is what closes the sources.
    """
    async with contextlib.AsyncExitStack() as stack:
        for gen in sources.values():
            await stack.enter_async_context(contextlib.aclosing(gen))

        pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
        while pulls:
            done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                key = pulls.pop(task)
                try:
                    item = task.result()
                except StopAsyncIteration:
                    continue  # this source is spent
                yield key, item
                pulls[asyncio.create_task(anext(sources[key]))] = key


async def main() -> None:
    sources = {
        'chatty': chatty(),
        'quiet1': quiet('quiet1'),
        'quiet2': quiet('quiet2'),
    }

    try:
        merged = merge(sources)
        async with contextlib.aclosing(merged):
            async for key, item in merged:
                print(f'got {key} {item}')
                break
    except RuntimeError as exc:
        print(f'teardown raised: {type(exc).__name__}: {exc}')

    print(f'sources whose finally ran: {finally_ran}')
    live = [name for name, gen in sources.items() if gen.ag_frame is not None]
    print(f'frames still live        : {live}')

    # Housekeeping, so the run exits without "Task was destroyed" noise.
    current = asyncio.current_task()
    leftover = [task for task in asyncio.all_tasks() if task is not current]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


asyncio.run(main())
