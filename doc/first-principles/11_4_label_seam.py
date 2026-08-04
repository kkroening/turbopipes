#!/usr/bin/env python3
"""§11.4 — the one thing the decomposition couldn't cut cleanly.

A source that raises from its own `finally` while its pull is being cancelled
has raised onto a cancelled task, on a path that is already unwinding, with no
consumer left to receive it.  `asettle` hands it to the event loop's exception
handler, which is the only channel it has — and a report that can't say *which*
source failed is a great deal less useful than one that can.

The layer holding the source's pull, and therefore the only one that can see
the failure, is `ataskify`.  The layer that knows the source's name is `atag`,
which arms nothing.  `amerge` sits above both and knows neither.  So the name
has to be handed down out of band, which is what `ataskify`'s `label` is for and
why `aselect` passes the key twice — once to `atag` for the consumer, once as
`label` for this report.

Three sources, two of them mid-pull with a `finally` that fails identically.
Merged keylessly, and again through `aselect`.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

Merge = Callable[
    [Mapping[str, AsyncGenerator[str, None]]],
    AsyncGenerator[Any, None],
]


class DeviceError(Exception):
    pass


def make_sources() -> dict[str, AsyncGenerator[str, None]]:
    async def chatty() -> AsyncGenerator[str, None]:
        index = 0
        while True:
            await asyncio.sleep(0)
            yield f'chatty{index}'
            index += 1

    async def sensor() -> AsyncGenerator[str, None]:
        try:
            await asyncio.Event().wait()  # suspended inside its own body
            yield 'unreachable'
        finally:
            raise DeviceError('could not release the device')

    return {'chatty': chatty(), 'sensor-a': sensor(), 'sensor-b': sensor()}


def keyless(
    sources: Mapping[str, AsyncGenerator[str, None]],
) -> AsyncGenerator[asyncio.Task[str], None]:
    return turbopipes.amerge(
        [turbopipes.ataskify(gen) for gen in sources.values()],
    )


async def run(label: str, merge: Merge) -> None:
    reported: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        stream = merge(make_sources())
        with contextlib.suppress(BaseException):
            async with contextlib.aclosing(stream):
                async for _item in stream:
                    break
    finally:
        loop.set_exception_handler(previous)

    print(f'{label}:')
    for context in reported:
        print(f'  {context["message"]}')
        print(f'    {type(context["exception"]).__name__}: {context["exception"]}')


async def main() -> None:
    await run('amerge over ataskify, no keys anywhere', keyless)
    await run('aselect, handing the key down as label', turbopipes.aselect)


asyncio.run(main())
