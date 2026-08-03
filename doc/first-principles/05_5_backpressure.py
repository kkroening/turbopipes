#!/usr/bin/env python3
"""§5.5 — what the re-arm line buys, and what it doesn't.

Two sources that never await, so they'd run away instantly if allowed, against a
consumer that dawdles twenty loop passes per item.  The merge is run twice: once
re-arming after the yield, as `aselect` does, and once re-arming before it.

Neither lets a source run away, because that guarantee doesn't come from this
line: the merge is an async generator holding at most one pull per source, so
between yields it isn't running, and while it isn't running it isn't arming
anything.  What moving the line changes is what this program reports — one
produced-but-unconsumed item across the merge rather than two.  The count is
merge-wide, since `produced` below is one counter shared by both sources;
counted per source neither position exceeds one item.  The merge-wide
difference comes from whether the source just consumed from stays armed while
the consumer is away.

Sampling point matters here and is easy to get wrong: `produced` is read after
the consumer has finished dawdling, by which time a pull armed during the
previous yield has been stepped.  Reading it at the instant of the consume
instead gives a different ladder for the same run.
"""

import asyncio
from collections.abc import AsyncGenerator
from collections.abc import Mapping

DAWDLE_PASSES = 20
CONSUME = 4


def make_sources(produced: list[int]) -> dict[str, AsyncGenerator[str, None]]:
    async def never_awaits(name: str) -> AsyncGenerator[str, None]:
        index = 0
        while True:
            produced[0] += 1
            yield f'{name}{index}'
            index += 1

    return {'a': never_awaits('a'), 'b': never_awaits('b')}


async def merge(
    sources: Mapping[str, AsyncGenerator[str, None]],
    rearm_before_yield: bool,
) -> AsyncGenerator[tuple[str, str], None]:
    pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
    while pulls:
        done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            key = pulls.pop(task)
            try:
                item = task.result()
            except StopAsyncIteration:
                continue
            if rearm_before_yield:
                pulls[asyncio.create_task(anext(sources[key]))] = key
                yield key, item
            else:
                yield key, item
                pulls[asyncio.create_task(anext(sources[key]))] = key


async def run(label: str, rearm_before_yield: bool) -> None:
    produced = [0]
    sources = make_sources(produced)
    merged = merge(sources, rearm_before_yield)

    print(f'{label}:')
    consumed = 0
    async for _key, _item in merged:
        consumed += 1
        for _ in range(DAWDLE_PASSES):
            await asyncio.sleep(0)
        print(
            f'  consumed {consumed}, produced {produced[0]}'
            f'  (ahead by {produced[0] - consumed})'
        )
        if consumed == CONSUME:
            break

    await merged.aclose()

    current = asyncio.current_task()
    leftover = [task for task in asyncio.all_tasks() if task is not current]
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


async def main() -> None:
    await run('re-arm AFTER the yield (what aselect does)', rearm_before_yield=False)
    await run('re-arm BEFORE the yield', rearm_before_yield=True)


asyncio.run(main())
