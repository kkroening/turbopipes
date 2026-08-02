#!/usr/bin/env python3
"""§4.3 — what leaving an `aparallel` loop early under `aclosing` does today.

This is the one script here that measures a defect rather than a design.  The
cleanup does its job — the source's `finally` runs — but the close raises on the
way out, and the consumer's own exception does not survive it.

See the footnote in ``first-principles.md``.  Nothing else in the harness depends
on this behaviour; the scripts that trip over it say so where they swallow it.
"""

import asyncio
import contextlib
import os
import sys
import traceback
from collections.abc import AsyncGenerator
from collections.abc import Awaitable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

import turbopipes  # noqa: E402  # pylint: disable=wrong-import-position

MAX_CONCURRENT = 8
MESSAGE = 'consumer said no'


async def work(index: int) -> int:
    await asyncio.sleep(0.01)
    return index


async def gen(state: dict[str, bool]) -> AsyncGenerator[Awaitable[int], None]:
    try:
        for index in range(100):
            yield work(index)
    finally:
        state['source_finally_ran'] = True


def mentions_the_consumers_exception(exc: BaseException) -> bool:
    """Searches group membership, `__context__` and `__cause__`, and the text."""
    found = MESSAGE in ''.join(traceback.format_exception(exc))
    pending: list[BaseException | None] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is not None and id(current) not in seen:
            seen.add(id(current))
            found = found or isinstance(current, ValueError)
            pending.append(current.__context__)
            pending.append(current.__cause__)
            if isinstance(current, BaseExceptionGroup):
                pending.extend(current.exceptions)
    return found


async def leave_early(how: str) -> tuple[BaseException | None, dict[str, bool]]:
    state: dict[str, bool] = {}
    pipeline = turbopipes.aparallel(gen(state), max_concurrent=MAX_CONCURRENT)
    escaped: BaseException | None = None
    try:
        async with contextlib.aclosing(pipeline):
            consumed = 0
            async for done_task in pipeline:
                await done_task
                consumed += 1
                if consumed == 2:
                    if how == 'raise':
                        raise ValueError(MESSAGE)
                    break
    except BaseException as exc:  # pylint: disable=broad-exception-caught
        escaped = exc
    return escaped, state


async def main() -> None:
    outcomes = {how: await leave_early(how) for how in ('break', 'raise')}

    for how, (escaped, _) in outcomes.items():
        summary = (
            'returned quietly' if escaped is None else f'{type(escaped).__name__}: {escaped}'
        )
        print(f'{how + ", under aclosing":<24}-> {summary}')

    raised, state = outcomes['raise']
    print(
        "  consumer's ValueError anywhere in the group, the chain, or the traceback? "
        f'{raised is not None and mentions_the_consumers_exception(raised)}'
    )
    print(f"  meanwhile, the source's finally ran? {state.get('source_finally_ran', False)}")


asyncio.run(main())
