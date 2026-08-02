import asyncio
from ._amerge import amerge
from ._atag import atag
from ._ataskify import ataskify
from collections.abc import AsyncGenerator
from collections.abc import Mapping
from typing import TypeVar

_K = TypeVar('_K')
_T = TypeVar('_T')


def aselect(
    gens: Mapping[_K, AsyncGenerator[_T, None]],
) -> AsyncGenerator[tuple[_K, asyncio.Task[_T]], None]:
    """Merges several keyed async generators into a single stream, in completion order.

    Items are yielded as ``(key, task)`` pairs as soon as any source produces one, where
    ``key`` is the mapping key of the source that produced it.  A consumer folding
    several event sources into one loop nearly always needs to know which source spoke,
    and needs to know it *before* awaiting - the failure of one source is the consumer's
    to handle, and which source it is may well decide how::

        async for key, task in aselect(sources):
            try:
                item = await task
            except Exception:
                ...  # this source failed; the others keep running

    This is sugar, and deliberately thin.  It is exactly

    .. code-block:: python

        amerge([atag(key, ataskify(gen, label=key)) for key, gen in gens.items()])

    which is worth reading rather than taking on faith, because the order of the three
    is the whole design.  :func:`~turbopipes.ataskify` is innermost, so a source failure
    becomes a task that raises on await rather than an exception that ends the merge;
    :func:`~turbopipes.atag` wraps *that*, so the key rides alongside the task and can be
    read before the value exists; :func:`~turbopipes.amerge` is outermost and merely
    interleaves, knowing nothing of keys or tasks.  Tagging the source directly instead
    would bury the key inside the task, where reading it means awaiting - and by then the
    decision it was needed for has already been made.

    Reach past this to the composition whenever it doesn't fit: drop
    :func:`~turbopipes.atag` when the events already identify themselves, drop
    :func:`~turbopipes.ataskify` when one source failing *should* end the merge, or keep
    both and swap the mapping for whatever else supplies the keys.  Nothing here is
    load-bearing - the behaviour, the ownership and the cleanup all belong to the three
    functions underneath, and their docstrings are where the details live.

    Note:
        ``label=key`` is not a redundant second copy of the tag.  The two carry the key
        to different places - :func:`~turbopipes.atag` to the consumer, ``label`` to the
        event loop's exception handler - and only the latter can name a source whose own
        cleanup fails while the merge is being torn down, since by then there is no
        consumer left to tell.  See :func:`~turbopipes.ataskify`.

    Warning:
        The caller is responsible for closing this generator if consumption stops early
        - e.g. via :func:`contextlib.aclosing` - as with the rest of this library.  The
        sources are owned and closed by the composition, as described in
        :func:`~turbopipes.amerge` and :func:`~turbopipes.ataskify`.

    Example::

        async def main() -> None:
            sources = {
                'clicks': read_clicks(),
                'ticks': read_ticks(),
            }
            stream = aselect(sources)
            async with contextlib.aclosing(stream):
                async for key, task in stream:
                    print(key, await task)
    """
    return amerge([atag(key, ataskify(gen, label=key)) for key, gen in gens.items()])
