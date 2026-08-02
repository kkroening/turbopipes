import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import Mapping
from typing import TypeVar

_K = TypeVar('_K')
_T = TypeVar('_T')


async def _pull(gen: AsyncGenerator[_T, None]) -> _T:
    """Advances ``gen`` by a single item.

    ``gen.__anext__()`` produces an ``async_generator_asend`` object rather than a
    coroutine, so it can't be handed to :func:`asyncio.create_task` directly; wrapping
    it in a coroutine makes the pull schedulable as a task.
    """
    return await anext(gen)


def _is_exhausted(task: asyncio.Task[_T]) -> bool:
    """Whether a completed pull means that its source ran out of items.

    A source cannot raise ``StopAsyncIteration`` out of its own body - Python converts
    that into ``RuntimeError: async generator raised StopAsyncIteration`` - so a
    ``StopAsyncIteration`` arriving here unambiguously signals exhaustion, and never a
    failure that the consumer might have wanted to see.  That leaves the exhaustion
    signal entirely inside the iteration protocol, rather than needing a sentinel value
    that would otherwise widen the type of the tasks that get yielded.
    """
    return not task.cancelled() and isinstance(task.exception(), StopAsyncIteration)


async def aselect(
    gens: Mapping[_K, AsyncGenerator[_T, None]],
) -> AsyncGenerator[tuple[_K, asyncio.Task[_T]], None]:
    """Merges several async generators into a single stream, in completion order.

    Items are yielded as ``(key, task)`` pairs as soon as any source produces one,
    where ``key`` is the mapping key of the source that produced it.  Merging by
    mapping rather than by plain sequence is deliberate: a consumer folding several
    event sources into one loop nearly always needs to know which source spoke.

    As with :func:`aparallel`, the completed pull is yielded as an
    :class:`asyncio.Task` rather than as a bare item, and for the same reason: awaiting
    is the consumer's move, so it's the consumer that decides what a given source's
    failure means.  One bad source therefore tears down neither its peers nor the merge
    - the failure simply surfaces at the ``await``::

        async for key, task in aselect(sources):
            try:
                item = await task
            except Exception:
                ...  # this source failed; the others keep running

    A source that runs out of items drops out of the merge silently; the merge itself
    ends only once every source is exhausted.

    Note:
        Backpressure is maintained across all of the sources: at most one pull is in
        flight per source, and a source is re-armed only once the consumer comes back
        for another item.  A fast source therefore can't run more than a single item
        ahead of a slow consumer, no matter how eagerly it would like to.

        When several sources complete within the same event loop pass, they're yielded
        in the order of ``gens`` rather than in the arbitrary order that
        :func:`asyncio.wait` reports them, so a merge of promptly-ready sources behaves
        reproducibly.

    Warning:
        As with the rest of this library, the caller is responsible for closing this
        generator if consumption stops early - e.g. via :meth:`contextlib.aclosing` -
        in order to avoid leaking the sources and their in-flight pulls.

        Unlike the rest of this library, though, ``aclosing`` applied to the *sources*
        would not have been sufficient by itself, which is worth understanding before
        rearranging the cleanup below.  Whenever consumption stops, every source that
        isn't parked mid-handoff is suspended at an ``await`` *inside its own body*,
        servicing an in-flight ``__anext__()``.  Such a generator has ``ag_running``
        set, and calling ``aclose()`` on it raises::

            RuntimeError: aclose(): asynchronous generator is already running

        That escapes from the cleanup path itself, masking the cancellation that was in
        progress and abandoning every source that hadn't been closed yet.  The in-flight
        pulls are therefore cancelled *and awaited* first, and only then are the sources
        closed.

        Both halves are load-bearing.  Cancelling a pull runs its source's ``finally``
        blocks, which covers every source that was mid-pull; but a source parked at its
        ``yield`` - one whose item was just handed to the consumer, or is queued to be -
        has no pull to cancel, and is closed by ``aclose()``.

        This generator takes ownership of the sources it's given, and closes all of them
        on the way out, whether it finishes normally, is closed early, or is cancelled.

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
    pulls: dict[asyncio.Task[_T], _K] = {}
    ready: list[tuple[_K, asyncio.Task[_T]]] = []

    def arm_pull(key: _K) -> None:
        pulls[asyncio.create_task(_pull(gens[key]))] = key

    async with contextlib.AsyncExitStack() as stack:
        for gen in gens.values():
            stack.push_async_callback(gen.aclose)

        try:
            for key in gens:
                arm_pull(key)

            while pulls or ready:
                if not ready:
                    done, _ = await asyncio.wait(
                        pulls, return_when=asyncio.FIRST_COMPLETED
                    )
                    # Filter `pulls` by `done` rather than iterating `done` directly, so
                    # that sources completing within the same pass come out in `gens`
                    # order rather than in arbitrary `set` order.
                    done_pulls = [pull for pull in pulls if pull in done]
                    for task in done_pulls:
                        ready.append((pulls.pop(task), task))

                key, task = ready.pop(0)
                if _is_exhausted(task):
                    continue  # the source is spent; don't re-arm it
                yield key, task
                arm_pull(key)  # only now that the consumer has come back for more
        finally:
            # In-flight pulls must be cancelled *and awaited* before any source is
            # closed, or `aclose()` lands on a generator that's still running; see the
            # warning above.  The sources are closed by the `AsyncExitStack`, i.e. only
            # once this block has finished, so that ordering is structural rather than
            # a matter of statement order - and holds even if this block is itself
            # interrupted.  Completed-but-unyielded pulls are awaited too, so that
            # their results and exceptions are retrieved rather than orphaned.
            for task in pulls:
                task.cancel()
            leftovers = [*pulls, *(task for _, task in ready)]
            if leftovers:
                await asyncio.gather(*leftovers, return_exceptions=True)
