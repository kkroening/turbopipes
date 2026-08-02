#!/usr/bin/env python3
"""§3 — the same worker pool against a consumer that merely dawdles.

Nobody has walked away here.  The consumer is still consuming, just slowly, and
the pool runs away from it regardless: the results queue is unbounded, so the
workers never block, so the feeder never blocks, so the source never stops.

Compare the ladder this prints with §4.1's, which is the same measurement
against `aparallel`.
"""

import asyncio
from collections.abc import AsyncGenerator

MAX_CONCURRENT = 5
DAWDLE_PASSES = 50

_produced = 0


async def work(item: int) -> int:
    await asyncio.sleep(0)
    return item


async def source() -> AsyncGenerator[int, None]:
    global _produced  # pylint: disable=global-statement
    for index in range(10000):
        _produced += 1
        yield index


async def naive_pool(
    gen: AsyncGenerator[int, None],
    results: asyncio.Queue[int],
    max_concurrent: int,
) -> list[asyncio.Task[None]]:
    queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=max_concurrent)

    async def feeder() -> None:
        async for item in gen:
            await queue.put(item)
        for _ in range(max_concurrent):
            await queue.put(None)

    async def worker() -> None:
        while True:
            item = await queue.get()
            if item is None:
                break
            await results.put(await work(item))

    return [asyncio.create_task(feeder())] + [
        asyncio.create_task(worker()) for _ in range(max_concurrent)
    ]


async def main() -> None:
    results: asyncio.Queue[int] = asyncio.Queue()
    tasks = await naive_pool(source(), results, MAX_CONCURRENT)

    for consumed in range(1, 5):
        await results.get()
        for _ in range(DAWDLE_PASSES):
            await asyncio.sleep(0)
        print(
            f'consumed {consumed}, source has produced {_produced}'
            f'  (ahead by {_produced - consumed})'
        )

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


asyncio.run(main())
