#!/usr/bin/env python3
"""§5.3 — `AsyncExitStack` runs every callback; it doesn't collect their failures.

Three sources parked at a `yield`, each with a `finally` that blows up.  All
three close.  One failure comes out.  The other two are not suppressed and not
chained onto it — they are gone, and nothing anywhere says so.

The stack *repairs* an exception chain that already exists; it does not build
one.  `_fix_exception_context` walks the new exception's `__context__` looking
for the place to splice the old one on, and gives up the moment it reaches a
`None` — which here it does immediately, because the escaping `CleanupError` was
raised from a `finally` running under the `GeneratorExit` that `aclose()` threw
in, and that `GeneratorExit`'s own context is `None`.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator

NAMES = ('p1', 'p2', 'p3')


class CleanupError(Exception):
    pass


async def parked(name: str) -> AsyncGenerator[str, None]:
    try:
        while True:
            yield f'{name}-item'
    finally:
        raise CleanupError(f'{name} cleanup blew up')


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
    sources = {name: parked(name) for name in NAMES}
    for gen in sources.values():
        await anext(gen)  # park each one at its `yield`

    caught: BaseException | None = None
    try:
        async with contextlib.AsyncExitStack() as stack:
            for gen in sources.values():
                await stack.enter_async_context(contextlib.aclosing(gen))
    except CleanupError as exc:
        caught = exc

    assert caught is not None
    print(f'escaped   : {type(caught).__name__}: {caught}')
    print(f'full chain: {chain_of(caught)}')
    print(f'all closed: {[sources[name].ag_frame is None for name in NAMES]}')


asyncio.run(main())
