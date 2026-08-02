import asyncio
from collections.abc import AsyncGenerator
from typing import TypeVar

_T = TypeVar('_T')


async def pull(gen: AsyncGenerator[_T, None]) -> _T:
    """Advances ``gen`` by a single item.

    Wrapping the pull is a readability choice rather than a necessity: the
    ``async_generator_asend`` object that ``gen.__anext__()`` returns happens to be
    schedulable directly on current CPython, but going through a coroutine keeps the
    callers dealing in ordinary types, and ``anext(gen)`` is the plain spelling of
    "advance this source by one".
    """
    return await anext(gen)


def is_exhausted(task: asyncio.Task[_T]) -> bool:
    """Whether a completed pull means that its source ran out of items.

    A source cannot raise ``StopAsyncIteration`` out of its own body - Python converts
    that into ``RuntimeError: async generator raised StopAsyncIteration`` - so a
    ``StopAsyncIteration`` arriving here unambiguously signals exhaustion, and never a
    failure that the consumer might have wanted to see.  That leaves the exhaustion
    signal entirely inside the iteration protocol, rather than needing a sentinel value
    that would otherwise widen the type of whatever gets yielded onward.
    """
    return not task.cancelled() and isinstance(task.exception(), StopAsyncIteration)
