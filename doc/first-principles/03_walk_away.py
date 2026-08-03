#!/usr/bin/env python3
"""§3 — the hand-rolled worker pool, after the consumer walks away.

Nobody told the workers, nobody told the feeder, nobody told the source.  Work
carries on being done for a consumer that has already left, and the source's
`finally` has not run.

The count of items completed after the walk-away is measured in event-loop
passes rather than wall-clock, so it is a property of the design rather than of
the machine it ran on.
"""

import asyncio
from collections.abc import AsyncGenerator

MAX_CONCURRENT = 5
PASSES_AFTER_WALKING_AWAY = 50

_completed: list[int] = []
_source_finally_ran = False


async def work(item: int) -> int:
    await asyncio.sleep(0)
    _completed.append(item)
    return item


async def source() -> AsyncGenerator[int, None]:
    global _source_finally_ran  # pylint: disable=global-statement
    try:
        for index in range(1000):
            yield index
    finally:
        _source_finally_ran = True


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
            await queue.put(None)  # one poison pill per worker

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

    for index in range(3):
        await results.get()
        print(f'consumed {index}')

    print('--- consumer walks away here ---')
    completed_at_walk_away = len(_completed)

    for _ in range(PASSES_AFTER_WALKING_AWAY):
        await asyncio.sleep(0)

    workers = tasks[1:]
    still_running = sum(1 for task in workers if not task.done())
    print(f'workers still running : {still_running} of {len(workers)}')
    print(f'source finally ran    : {_source_finally_ran}')
    print(
        'work items completed since we stopped consuming: '
        f'{len(_completed) - completed_at_walk_away}'
    )

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


asyncio.run(main())
