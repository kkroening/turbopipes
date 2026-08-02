import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import Mapping
from typing import TypeVar

_K = TypeVar('_K')
_T = TypeVar('_T')


async def _pull(gen: AsyncGenerator[_T, None]) -> _T:
    """Advances ``gen`` by a single item.

    Wrapping the pull is a readability choice rather than a necessity: the
    ``async_generator_asend`` object that ``gen.__anext__()`` returns happens to be
    schedulable directly on current CPython, but going through a coroutine keeps the
    merge loop dealing in ordinary types, and ``anext(gen)`` is the plain spelling of
    "advance this source by one".
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


def _report_cleanup_failure(key: _K, task: asyncio.Task[_T]) -> None:
    """Reports a source's own cleanup failure, if its cancelled pull carries one.

    A source that was still mid-pull when the merge was torn down is cancelled, and
    anything it raises out of its own ``finally`` lands on that cancelled pull rather
    than on any caller: by then the merge is already unwinding and there's nobody left
    to raise it to.  Handing it to :meth:`asyncio.loop.call_exception_handler` -
    asyncio's own route for an exception that nobody can receive - keeps the failure
    visible without re-raising it here, where it would only displace the
    ``GeneratorExit`` or ``CancelledError`` that's unwinding the merge.

    A cancelled pull usually carries no such failure: the source may have propagated
    the cancellation; or caught it and returned, ending its own iteration, so that the
    pull raises ``StopAsyncIteration`` rather than cancelling; or swallowed it and
    yielded once more, leaving the pull with an ordinary value.  Only an exception
    raised while unwinding is a cleanup failure.

    A pull that had already *completed* before the teardown reached it isn't one
    either - it carries whatever the source produced or raised on its own account -
    but that case never arrives here, because the caller hands over only the pulls it
    actually cancelled.

    Of the exceptions a source can raise out of its own ``finally``,
    ``KeyboardInterrupt`` and ``SystemExit`` are the only two that never arrive here -
    and it isn't the ``return_exceptions=True`` gather that keeps them out.
    :class:`asyncio.Task`'s step handler re-raises those two types specifically into the
    event loop after storing them, so the run comes apart before the teardown reaches
    this reporting.  Every other ``BaseException`` is stored by that gather like any
    other exception, arrives here, and is reported - and therefore swallowed, since
    reporting deliberately doesn't re-raise.  That's the intended outcome for a cleanup
    failure whatever it derives from, but it's worth knowing before touching the gather,
    which isn't the thing drawing that line.
    """
    exc = None if task.cancelled() or _is_exhausted(task) else task.exception()
    if exc is not None:
        asyncio.get_running_loop().call_exception_handler(
            {
                'message': (
                    f'aselect: source {key!r} raised during its own cleanup while the '
                    f'merge was being torn down; there was no consumer left to raise '
                    f'it to'
                ),
                'exception': exc,
                'task': task,
            }
        )


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
        least-recently-served first, rather than in the arbitrary order that
        :func:`asyncio.wait` reports them: a source drops to the back of the queue once
        it's been served, and sources that haven't yet produced anything are ordered
        among themselves by their position in ``gens``.  A merge of promptly-ready
        sources is therefore both reproducible and starvation-free - a source can't be
        crowded out by busier peers, however far down ``gens`` it sits.

    Warning:
        As with the rest of this library, the caller is responsible for closing this
        generator if consumption stops early - e.g. via :meth:`contextlib.aclosing` -
        in order to avoid leaking the sources and their in-flight pulls.

        Unlike the rest of this library, though, ``aclosing`` applied to the *sources*
        would not have been sufficient by itself, which is worth understanding before
        rearranging the cleanup below.  Whenever consumption stops, every source that's
        neither exhausted nor parked mid-handoff is suspended at an ``await`` *inside
        its own body*, servicing an in-flight ``__anext__()``.  Such a generator has
        ``ag_running`` set, and calling ``aclose()`` on it raises::

            RuntimeError: aclose(): asynchronous generator is already running

        That escapes from the cleanup path itself, masking the cancellation that was in
        progress and leaving every mid-pull source uncleaned - ``aclose()`` fails on
        each of them in turn.  It stops there rather than cascading: the
        ``AsyncExitStack`` runs its remaining callbacks even after one of them raises,
        so a source parked at its ``yield`` is still closed, however many failures it
        sits behind.  The in-flight pulls are therefore cancelled *and awaited* first,
        and only then are the sources closed.

        Both halves are load-bearing.  Cancelling a pull runs its source's ``finally``
        blocks, which covers every source that was mid-pull; but a source parked at its
        ``yield`` - one whose item was just handed to the consumer, or is queued to be -
        has no pull to cancel, and is closed by ``aclose()``.

        This generator takes ownership of the sources it's given, and closes all of them
        on the way out, whether it finishes normally, is closed early, or is cancelled.
        That ownership begins when the merge does, though: like any async generator,
        this one runs none of its body - including the registration of those closes -
        until it's first advanced.  A merge that's closed without ever having been
        advanced therefore leaves its sources untouched, and they're still the caller's
        to close at that point.

        There's a real asymmetry in how a source's *own* cleanup failure surfaces, and
        which way it goes isn't something the caller controls: it turns on where that
        source happened to be suspended when consumption stopped.  A source parked at
        its ``yield`` is closed via ``aclose()``, so anything raised out of its
        ``finally`` propagates to whoever closed the merge - the better outcome when
        the merge is being closed *explicitly*, and the reason that path is left as it
        is.  It costs something when the merge is being *cancelled* instead: the
        propagated failure replaces the ``CancelledError``, so a cancelled consumer
        surfaces as having raised that failure, on a task that reports itself as not
        cancelled - and an :func:`asyncio.timeout` around the merge ends in the
        source's exception rather than in ``TimeoutError``.  Since the sources are this
        generator's to close, a single one with a failing ``finally`` is enough to do
        that, which is worth weighing before running a merge inside an
        :class:`asyncio.TaskGroup` or under a timeout.

        A source that was *mid-pull* when consumption stopped is cancelled rather than
        closed, and its failure lands on the cancelled pull, at a point where the merge
        is already unwinding and no consumer remains to receive it; re-raising it there
        would only displace the ``GeneratorExit`` or ``CancelledError`` doing the
        unwinding - the same hazard, deliberately not realised on this side.  It's
        therefore passed to the event loop's exception handler (see
        :meth:`asyncio.loop.call_exception_handler`) rather than raised: reported and
        logged, but not propagated.

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
                    # that sources completing within the same pass come out in a
                    # deterministic round-robin order rather than in arbitrary `set`
                    # order.  `pulls` is keyed in arming order, and a source isn't
                    # re-armed until it's been served, so serving one sends it to the
                    # back - which is why this is least-recently-served first, and not
                    # `gens` order beyond the first pass.
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
            cancelled_pulls = {
                task: key for task, key in pulls.items() if not task.done()
            }
            for task in cancelled_pulls:
                task.cancel()
            leftovers = [*pulls, *(task for _, task in ready)]
            if leftovers:
                await asyncio.gather(*leftovers, return_exceptions=True)

            # Only a pull that was still running when it was cancelled can carry a
            # source's own cleanup failure; one that had already completed carries an
            # ordinary result or failure that the consumer simply walked away from.
            for task, key in cancelled_pulls.items():
                _report_cleanup_failure(key, task)
