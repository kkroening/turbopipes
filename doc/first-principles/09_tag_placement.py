#!/usr/bin/env python3
"""§9 — where `atag` goes, and why the appealing order is the wrong one.

`atag` is three lines and has no interesting behaviour of its own.  What it has
is a position in the stack, and the two candidate positions differ in a way that
only shows up when a source fails.

    atag(key, ataskify(gen))  ->  tuple[str, Task[str]]   key beside the task
    ataskify(atag(key, gen))  ->  Task[tuple[str, str]]   key inside the task

Reading the key out of the second means awaiting the task — and if the task
raises, the await produces the exception instead of the pair, so the key is
gone at exactly the moment it was wanted.

The consumer below has a per-source policy: a dropped connection from `feed` is
routine and gets retried, while anything from `ledger` is fatal.  It has to pick
the policy from the key, so it has to have the key before it awaits.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncGenerator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

TAKE = 5
POLICY = {'ledger': 'fatal', 'feed': 'retry'}


def make_sources() -> dict[str, AsyncGenerator[str, None]]:
    async def ledger() -> AsyncGenerator[str, None]:
        for index in range(10):
            await asyncio.sleep(0)
            yield f'entry{index}'

    async def feed() -> AsyncGenerator[str, None]:
        await asyncio.sleep(0)
        yield 'quote'
        await asyncio.sleep(0)
        raise ConnectionResetError('socket went away')

    return {'ledger': ledger(), 'feed': feed()}


async def key_beside_the_task() -> None:
    sources = make_sources()
    stream = turbopipes.amerge(
        [
            turbopipes.atag(key, turbopipes.ataskify(gen))
            for key, gen in sources.items()
        ],
    )

    log: list[str] = []
    async with contextlib.aclosing(stream):
        async for key, task in stream:
            try:
                log.append(f'{key}={await task}')
            except Exception as exc:  # pylint: disable=broad-exception-caught
                log.append(f'{key} {type(exc).__name__} -> {POLICY[key]}')
            if len(log) == TAKE:
                break

    print('atag(key, ataskify(gen))  ->  tuple[str, Task[str]]')
    for line in log:
        print(f'  {line}')


async def key_inside_the_task() -> None:
    sources = make_sources()
    stream = turbopipes.amerge(
        [
            turbopipes.ataskify(turbopipes.atag(key, gen))
            for key, gen in sources.items()
        ],
    )

    log: list[str] = []
    async with contextlib.aclosing(stream):
        async for task in stream:
            try:
                key, item = await task
                log.append(f'{key}={item}')
            except Exception as exc:  # pylint: disable=broad-exception-caught
                log.append(f'? {type(exc).__name__} -> no policy; no key')
            if len(log) == TAKE:
                break

    print('ataskify(atag(key, gen))  ->  Task[tuple[str, str]]')
    for line in log:
        print(f'  {line}')


async def main() -> None:
    await key_beside_the_task()
    await key_inside_the_task()


asyncio.run(main())
