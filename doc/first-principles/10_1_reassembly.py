#!/usr/bin/env python3
"""§10.1 — the three pieces put back together.

`aselect` is one expression over the three:

    amerge([atag(key, ataskify(gen, label=key)) for key, gen in gens.items()])

The claim this checks is that the expression *is* the function rather than
merely resembling it.  Both are run against the same scenario — three sources on
different periods, one of which fails part-way through — and the delivered
`(key, task)` pairs are compared, failures included.

Each delivery is printed as the source's key followed by the item it carried,
or by `!` where awaiting the task raised instead.  What is not compared here is
anything about teardown; that is §11's subject.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Mapping

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

PERIODS = {'log': 1, 'tick': 2, 'feed': 3}
ITEMS = 3
FAILS_AT = 1

Merge = Callable[
    [Mapping[str, AsyncGenerator[str, None]]],
    AsyncGenerator[tuple[str, asyncio.Task[str]], None],
]


def make_sources() -> dict[str, AsyncGenerator[str, None]]:
    def paced(name: str, period: int) -> AsyncGenerator[str, None]:
        async def gen() -> AsyncGenerator[str, None]:
            for index in range(ITEMS):
                for _ in range(period):
                    await asyncio.sleep(0)
                if name == 'feed' and index == FAILS_AT:
                    raise ConnectionResetError('socket went away')
                yield str(index)

        return gen()

    return {name: paced(name, period) for name, period in PERIODS.items()}


def hand_composed(
    gens: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[tuple[str, asyncio.Task[str]], None]:
    return turbopipes.amerge(
        [
            turbopipes.atag(key, turbopipes.ataskify(gen, label=key))
            for key, gen in gens.items()
        ],
    )


async def drain(merge: Merge) -> list[str]:
    stream = merge(make_sources())
    delivered: list[str] = []
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            try:
                delivered.append(f'{key}{await task}')
            except Exception:  # pylint: disable=broad-exception-caught
                delivered.append(f'{key}!')
    return delivered


async def main() -> None:
    composed = await drain(hand_composed)
    sugar = await drain(turbopipes.aselect)

    print(f'amerge/atag/ataskify: {" ".join(composed)}')
    print(f'turbopipes.aselect  : {" ".join(sugar)}')
    print(f'identical           : {composed == sugar}')


asyncio.run(main())
