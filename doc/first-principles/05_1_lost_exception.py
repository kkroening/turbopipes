#!/usr/bin/env python3
"""§5.1 — where the `RuntimeError` raises: out of the cleanup path itself.

The same merge as §5 — one chatty source, two suspended mid-pull — torn down
because the consumer raised a `ValueError`.  What the caller receives is a
complaint about generator state, and their own `ValueError` is nowhere in it.

Each mid-pull source contributes its own `RuntimeError`, so the chain carries one
per source that was mid-pull, ending at the `GeneratorExit` that `aclose()` threw
in.  That `GeneratorExit` has no context of its own, which is where the
`ValueError` would have been.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import Mapping


async def chatty() -> AsyncGenerator[str, None]:
    index = 0
    while True:
        await asyncio.sleep(0)
        yield f'chatty{index}'
        index += 1


async def quiet(name: str) -> AsyncGenerator[str, None]:
    await asyncio.Event().wait()  # suspended inside the body; nothing ever sets it
    yield f'{name}-unreachable'


async def merge(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, str], None]:
    """The merge owns its sources, the way `aselect` does."""
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
                    continue
                yield key, item
                pulls[asyncio.create_task(anext(sources[key]))] = key


def chain_of(exc: BaseException) -> list[str]:
    chain = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f'{type(current).__name__}: {current}')
        current = current.__context__
    return chain


async def main() -> None:
    sources = {
        'chatty': chatty(),
        'quiet1': quiet('quiet1'),
        'quiet2': quiet('quiet2'),
    }

    caught: BaseException | None = None
    try:
        merged = merge(sources)
        async with contextlib.aclosing(merged):
            async for _key, _item in merged:
                raise ValueError('the consumer gave up')
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        caught = exc

    assert caught is not None
    chain = chain_of(caught)
    print(f'the caller sees: {type(caught).__name__}: {caught}')
    for index, entry in enumerate(chain):
        label = 'chain          :' if index == 0 else ' ' * 16
        print(f'{label} {entry}')
    found = any(entry.startswith('ValueError') for entry in chain)
    print(f"the consumer's ValueError anywhere in it: {found}")

    current = asyncio.current_task()
    leftover = [task for task in asyncio.all_tasks() if task is not current]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


asyncio.run(main())
