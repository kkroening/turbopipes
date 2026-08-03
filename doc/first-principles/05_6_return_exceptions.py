#!/usr/bin/env python3
"""§5.6 — what `return_exceptions=True` does not bound.

It is natural to read the flag as drawing a line at `Exception` — collecting the
ordinary failures, letting the serious ones through.  It draws no such line:
`gather` stores a `BaseException` as readily as a `ValueError`.

What keeps `KeyboardInterrupt` and `SystemExit` out of that list is a different
mechanism one layer down — `Task`'s step handler, which special-cases exactly
those two, storing them *and* re-raising into the loop.
"""

import asyncio


class Boom(BaseException):
    pass


async def raises(exc: BaseException) -> None:
    await asyncio.sleep(0)
    raise exc


async def main() -> None:
    for label, exc in (('Exception    ', ValueError('v')), ('BaseException', Boom('b'))):
        results = await asyncio.gather(raises(exc), return_exceptions=True)
        print(f'{label}: gather returned {results!r}')

    task = asyncio.create_task(raises(KeyboardInterrupt()))
    try:
        await asyncio.gather(task, return_exceptions=True)
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        stored = task.exception() if task.done() and not task.cancelled() else None
        print(
            f'KeyboardInterrupt: gather RAISED {type(exc).__name__} '
            f'| task.exception() -> {type(stored).__name__}'
        )
        raise


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print('escaped asyncio.run: KeyboardInterrupt')
