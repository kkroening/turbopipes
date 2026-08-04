#!/usr/bin/env python3
"""§11.5 — the report's one gap today.

This is the second script here that measures a defect rather than a design, and
it is expected to change when the defect is fixed.  See the footnote in
``part-2-taking-it-apart.md``.

`asettle` cancels the pulls, waits for them with
`gather(..., return_exceptions=True)`, and then reports whatever cleanup failure
each cancelled pull came back holding.  The reporting is after the wait, and the
wait does not always return.  A further `cancel()` arriving while that gather is
open cancels the *gather*, which then raises `CancelledError` once its children
finish — `return_exceptions=True` governs what the children raise, not what is
done to the gather itself, which is §5.6's lesson arriving in a second costume.
Leaving by that route skips the reporting entirely.

The window is the whole duration of the teardown's gather, so it widens with
however long the sources take to clean up.  Below, the gap between the two
cancellations is swept in event-loop passes, against sources whose own cleanup
awaits once and three times.  `1` means the failure was reported; `0` means it
was dropped.
"""

import asyncio
import contextlib
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

GAPS = range(12)


class DeviceError(Exception):
    pass


async def blocked(cleanup_awaits: int) -> None:
    try:
        await asyncio.Event().wait()  # nothing ever sets it
    finally:
        for _ in range(cleanup_awaits):
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(0)  # outlive the second cancellation...
        raise DeviceError('could not release the device')  # ...and then fail


async def trial(gap: int, cleanup_awaits: int) -> int:
    reported: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        pull = asyncio.create_task(blocked(cleanup_awaits))
        for _ in range(3):
            await asyncio.sleep(0)

        async def consumer() -> None:
            try:
                await asyncio.Event().wait()  # nothing ever sets it
            finally:
                await turbopipes.asettle([pull], label='sensor')

        consuming = asyncio.create_task(consumer())
        for _ in range(3):
            await asyncio.sleep(0)

        consuming.cancel()  # the one that drives the consumer into its teardown
        for _ in range(gap):
            await asyncio.sleep(0)
        consuming.cancel()  # the one that lands inside the teardown's gather
        with contextlib.suppress(BaseException):
            await consuming
    finally:
        loop.set_exception_handler(previous)
    return len(reported)


async def main() -> None:
    for cleanup_awaits in (1, 3):
        row = [await trial(gap, cleanup_awaits) for gap in GAPS]
        label = f'cleanup awaits {cleanup_awaits}x'
        print(f'{label:<22}' + ' '.join(f'{value:>2}' for value in row))
    print(' ' * 22 + ' '.join(f'{gap:>2}' for gap in GAPS))
    print(' ' * 23 + '^ event-loop passes between the two cancellations')


asyncio.run(main())
