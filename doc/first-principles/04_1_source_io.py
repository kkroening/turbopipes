#!/usr/bin/env python3
"""§4.1 — the source's own I/O stays under the consumer's control.

A realistic source doesn't have the items lying around; it fetches them a page at
a time.  Written as an async generator, producing the next item is itself an
`await`, so *not pulling* means *not doing that I/O*.  Materialize the same work
into a list and every page is paid for before the pipeline runs a single item.
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

PAGES = 20
PER_PAGE = 3
MAX_CONCURRENT = 4
CONSUMED_BEFORE_STOPPING = 3


@contextlib.contextmanager
def tolerating_the_teardown_defect() -> Iterator[None]:
    """Swallows the `aparallel` early-exit defect, and nothing else.

    Leaving an `aparallel` loop early under `aclosing` currently raises a
    `BaseExceptionGroup` wrapping `GeneratorExit` — see the footnote in
    ``first-principles.md``, and ``04_3_early_exit_today.py`` for the
    measurement.  The cleanup itself is correct and the measurement below is
    unaffected, so this swallows exactly that group and re-raises anything else.
    """
    try:
        yield
    except BaseExceptionGroup as group:
        _, unexpected = group.split(GeneratorExit)
        if unexpected is not None:
            raise


async def fetch_page(page: int, pages_fetched: list[int]) -> list[int]:
    pages_fetched[0] += 1
    await asyncio.sleep(0)
    return [page * PER_PAGE + offset for offset in range(PER_PAGE)]


async def work(item: int) -> int:
    await asyncio.sleep(0)
    return item


async def gen(pages_fetched: list[int]) -> AsyncGenerator[Awaitable[int], None]:
    for page in range(PAGES):
        for item in await fetch_page(page, pages_fetched):  # the source's own I/O
            yield work(item)


async def via_async_generator() -> None:
    pages_fetched = [0]
    pipeline = turbopipes.aparallel(gen(pages_fetched), max_concurrent=MAX_CONCURRENT)

    with tolerating_the_teardown_defect():
        async with contextlib.aclosing(pipeline):
            consumed = 0
            async for done_task in pipeline:
                await done_task
                consumed += 1
                if consumed == CONSUMED_BEFORE_STOPPING:
                    break

    print(
        'async generator source: pages fetched before the consumer stopped: '
        f'{pages_fetched[0]}'
    )


async def via_materialized_list() -> None:
    pages_fetched = [0]
    items = [item for page in range(PAGES) for item in await fetch_page(page, pages_fetched)]
    assert len(items) == PAGES * PER_PAGE
    print(
        'materialized list      : pages fetched before the pipeline even started: '
        f'{pages_fetched[0]}'
    )


async def main() -> None:
    await via_async_generator()
    await via_materialized_list()


asyncio.run(main())
