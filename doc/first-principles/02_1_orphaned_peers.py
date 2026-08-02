#!/usr/bin/env python3
"""§2.1 — `gather` raises out of your `await` and leaves the peers running.

One bad item among three.  The peers outlive the `gather` they belonged to, run
to completion, and put their results nowhere.
"""

import asyncio
import contextlib

PEERS = ('a', 'b')


async def peer(name: str, status: dict[str, str]) -> None:
    try:
        await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        status[name] = f'{name} CANCELLED'
        raise
    status[name] = name


async def bad() -> None:
    await asyncio.sleep(0.01)
    raise ValueError('bad item')


async def main() -> None:
    status: dict[str, str] = {}
    peers = [asyncio.ensure_future(peer(name, status)) for name in PEERS]

    with contextlib.suppress(ValueError):
        await asyncio.gather(*peers, bad())

    # The `gather` has already raised; give the peers room to finish anyway.
    await asyncio.sleep(0.1)
    await asyncio.gather(*peers, return_exceptions=True)

    print(f'{"asyncio.gather":<23}peers -> {[status[name] for name in PEERS]}')


asyncio.run(main())
