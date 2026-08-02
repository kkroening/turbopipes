#!/usr/bin/env python3
"""§5.2 — what does reach a running generator: cancellation.

The `CancelledError` is delivered at the `await` inside the generator's body,
which is an entirely ordinary place to receive one, and unless the source catches
it and carries on, the generator unwinds.

Note the third line.  For a source that lets the cancellation through, cancelling
its pull is not half a teardown that `aclose()` still has to finish — it is a
complete one.
"""

import asyncio
from collections.abc import AsyncGenerator

log: list[str] = []


async def src() -> AsyncGenerator[str, None]:
    try:
        await asyncio.Event().wait()  # suspended inside the body; nothing ever sets it
        yield 'unreachable'
    finally:
        log.append('finally ran')


async def main() -> None:
    gen = src()
    pull = asyncio.create_task(anext(gen))
    await asyncio.sleep(0)

    pull.cancel()
    await asyncio.gather(pull, return_exceptions=True)
    print(f'after cancel: log = {log}')
    print(
        f'after cancel: ag_frame is None = {gen.ag_frame is None} '
        f'| ag_running = {gen.ag_running}'
    )

    await gen.aclose()
    print(
        f'later aclose(): returned; log = {log} '
        f'(finally did not run twice)'
    )


asyncio.run(main())
