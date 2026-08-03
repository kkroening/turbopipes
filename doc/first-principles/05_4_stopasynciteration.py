#!/usr/bin/env python3
"""§5.4 — exhaustion doesn't need a sentinel.

An async generator's body cannot hand you a `StopAsyncIteration` of its own; PEP
525 has the interpreter convert it.  So a `StopAsyncIteration` arriving on a pull
task means exhaustion and nothing else — never a failure the consumer might have
wanted to see.
"""

import asyncio
from collections.abc import AsyncGenerator


async def src() -> AsyncGenerator[str, None]:
    raise StopAsyncIteration('from the body')
    yield  # pylint: disable=unreachable


async def main() -> None:
    try:
        await anext(src())
    except RuntimeError as exc:
        print(f'{type(exc).__name__}: {exc}')
        print(f'  __cause__: {exc.__cause__!r}')


asyncio.run(main())
