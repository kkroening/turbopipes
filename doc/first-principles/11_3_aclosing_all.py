#!/usr/bin/env python3
"""§11.3 — what `aclosing_all` buys, and what it doesn't.

The appealing story is that it keeps closing after a close that raises, where
the alternatives give up.  Half of that is true.  The construction that gives up
is the *sequential loop*, whose first failure abandons the rest of it; a nested
stack of `aclosing` blocks keeps going just as `aclosing_all` does, because
`aclosing.__aexit__` ignores its exception arguments and closes unconditionally.

So the guarantee is not the differentiator.  What is, is *dynamic arity*: a
nested stack is written lexically, one `async with` per generator, which can't
be done over a sequence whose length is only known at runtime.  `AsyncExitStack`
is how that stack is built programmatically, and `aclosing_all` is that pattern
packaged with the settling caveat attached to it.

Three sources parked at a `yield`, the first two of which raise from their own
`finally`.  Closed three ways.  §5.3's other lesson shows up in all of them:
every source closes, and one failure comes out.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

NAMES = ('p1', 'p2', 'p3')

Closer = Callable[[Sequence[AsyncGenerator[str, None]]], Awaitable[None]]


class CleanupError(Exception):
    pass


def make_sources(closed: list[str]) -> list[AsyncGenerator[str, None]]:
    async def parked(name: str) -> AsyncGenerator[str, None]:
        try:
            while True:
                yield f'{name}-item'
        finally:
            closed.append(name)
            if name != 'p3':
                raise CleanupError(f'{name} cleanup blew up')

    return [parked(name) for name in NAMES]


async def via_sequential_loop(gens: Sequence[AsyncGenerator[str, None]]) -> None:
    for gen in gens:
        await gen.aclose()


async def via_nested_blocks(gens: Sequence[AsyncGenerator[str, None]]) -> None:
    first, second, third = gens
    async with contextlib.aclosing(first):
        async with contextlib.aclosing(second):
            async with contextlib.aclosing(third):
                pass


async def via_aclosing_all(gens: Sequence[AsyncGenerator[str, None]]) -> None:
    async with turbopipes.aclosing_all(gens):
        pass


async def run(label: str, closer: Closer) -> None:
    closed: list[str] = []
    gens = make_sources(closed)
    for gen in gens:
        await anext(gen)  # park each one at its `yield`

    escaped = 'nothing'
    try:
        await closer(gens)
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        escaped = f'{type(exc).__name__}: {exc}'
    by_the_closer = [*closed]

    for gen in gens:  # sweep up whatever the closer abandoned, so the run is quiet
        with contextlib.suppress(BaseException):
            await gen.aclose()

    print(
        f'{label}  closed {len(by_the_closer)} of {len(NAMES)}: {by_the_closer} '
        f'| escaped {escaped}'
    )


async def main() -> None:
    await run('for gen in gens: aclose()', via_sequential_loop)
    await run('nested aclosing blocks   ', via_nested_blocks)
    await run('aclosing_all(gens)       ', via_aclosing_all)


asyncio.run(main())
