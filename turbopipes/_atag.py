import contextlib
from collections.abc import AsyncGenerator
from typing import TypeVar

_K = TypeVar('_K')
_T = TypeVar('_T')


async def atag(
    key: _K,
    gen: AsyncGenerator[_T, None],
) -> AsyncGenerator[tuple[_K, _T], None]:
    """Attaches a constant key to everything an async generator yields.

    Deliberately indifferent to what it is tagging: ``atag`` is how identity is added to
    a stream that doesn't carry it, whatever that stream is made of.

    What it is *usually* wrapped around is worth stating, though, because the order
    matters and the appealing order is the wrong one.  Tagging a
    :func:`~turbopipes.ataskify` generator - so that the key rides alongside a task
    rather than alongside a value - keeps the identity available *before* the value
    exists::

        async for key, task in atag('clicks', ataskify(read_clicks())):
            item = await task  # ...and `key` was already known, before this line

    Tagging the source directly instead would put the key inside the task, where it
    can't be read without awaiting - and by then the pull has already happened, so a
    consumer that wanted to know which source it was dealing with *in order to decide
    how to await it* has missed its chance.  The distinction only shows up in the
    failure case, which is where it matters most.

    Warning:
        This generator takes ownership of ``gen`` and closes it on the way out, whether
        it finishes normally, is closed early, or is cancelled.

        It holds no tasks of its own, so it needs no cleanup beyond that: a cancellation
        arriving while it waits on ``gen`` is delivered *into* ``gen``'s own body, and
        whatever cleanup lives there runs on its own account.  That is what lets a chain
        of these unwind correctly from a single cancellation at the top.
    """
    async with contextlib.aclosing(gen):
        async for item in gen:
            yield key, item
