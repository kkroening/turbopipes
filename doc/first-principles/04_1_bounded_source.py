#!/usr/bin/env python3
"""§4.1 — the bound on how far the source can run ahead is real, not aspirational.

A consumer that dawdles fifty event-loop passes between items, against a source
that would happily produce a thousand.  `aparallel` is itself an async generator,
so between `yield`s it isn't running, and while it isn't running it isn't pulling.

This is the same measurement as `03_backpressure.py`, which runs it against the
hand-rolled worker pool and watches the gap grow without bound.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Iterator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

MAX_CONCURRENT = 4
DAWDLE_PASSES = 50
CONSUME = 4


@contextlib.contextmanager
def tolerating_the_teardown_defect() -> Iterator[None]:
    """Swallows the `aparallel` early-exit defect, and nothing else.

    See ``04_3_early_exit_today.py`` for the measurement, and the footnote in
    ``first-principles.md``.
    """
    try:
        yield
    except BaseExceptionGroup as group:
        _, unexpected = group.split(GeneratorExit)
        if unexpected is not None:
            raise


async def work(item: int) -> int:
    await asyncio.sleep(0)
    return item


async def gen(produced: list[int]) -> AsyncGenerator[Awaitable[int], None]:
    for index in range(1000):
        produced[0] += 1
        yield work(index)


async def main() -> None:
    produced = [0]
    pipeline = turbopipes.aparallel(gen(produced), max_concurrent=MAX_CONCURRENT)

    with tolerating_the_teardown_defect():
        async with contextlib.aclosing(pipeline):
            consumed = 0
            async for done_task in pipeline:
                await done_task
                consumed += 1
                for _ in range(DAWDLE_PASSES):
                    await asyncio.sleep(0)
                print(
                    f'consumed {consumed}, source has produced {produced[0]}'
                    f'  (ahead by {produced[0] - consumed})'
                )
                if consumed == CONSUME:
                    break


asyncio.run(main())
