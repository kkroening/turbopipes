#!/usr/bin/env python3
"""§5.1 — `aclose()` will not touch a generator that's inside its own body.

A source part-way through serving an `__anext__()` is suspended at an `await`
inside its own body rather than parked at a `yield`, and that is the state
`aclose()` refuses.

This is the one measurement the document ships complete; the code below is what
appears on the page.
"""

import asyncio
from collections.abc import AsyncGenerator


async def src() -> AsyncGenerator[str, None]:
    try:
        await asyncio.Event().wait()  # suspended inside the body; nothing ever sets it
        yield 'unreachable'
    finally:
        print('  src finally ran')


async def main() -> None:
    gen = src()
    pull = asyncio.create_task(anext(gen))
    await asyncio.sleep(0)
    print('ag_running:', gen.ag_running, ' ag_frame is None:', gen.ag_frame is None)
    try:
        await gen.aclose()
    except RuntimeError as exc:
        print(f'aclose() raised: {type(exc).__name__}: {exc}')
    pull.cancel()
    await asyncio.gather(pull, return_exceptions=True)


asyncio.run(main())
