import asyncio
from ._aclosing import aclosing_all
from ._aclosing import asettle
from ._pulling import is_exhausted
from ._pulling import pull
from collections.abc import AsyncGenerator
from collections.abc import Iterable
from typing import TypeVar

_T = TypeVar('_T')


async def amerge(
    gens: Iterable[AsyncGenerator[_T, None]],
) -> AsyncGenerator[_T, None]:
    """Merges several async generators into a single stream, in completion order.

    Items are yielded as soon as any source produces one, so a slow source never holds
    up a fast one.  A source that runs out of items drops out silently; the merge itself
    ends once every source is exhausted.

    A source that *fails* brings the whole merge down with it, and that is the deliberate
    difference from :func:`~turbopipes.aselect`.  It mirrors what an ``async for`` over a
    single failing generator already does - the iteration ends, whatever the generator
    would have produced next - and extends it to the obvious consequence for a merge: the
    other sources come down too, however innocent.  A consumer that wants failures
    isolated per source wraps its sources in :func:`~turbopipes.ataskify` first, which
    turns each failure into a task that raises on await rather than into an exception
    torn out of the iteration.

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
        The caller is responsible for closing this generator if consumption stops early
        - e.g. via :func:`contextlib.aclosing` - as with the rest of this library.

        This generator takes ownership of the sources it's given and closes all of them
        on the way out, whether it finishes normally, is closed early, or is cancelled.
        Ownership begins when the merge does: like any async generator, this one runs
        none of its body until first advanced, so a merge closed without ever having
        been advanced leaves its sources untouched and still the caller's to close.

        The cleanup is in two phases and both are load-bearing.  A source part-way
        through serving an ``__anext__()`` is suspended at an ``await`` inside its own
        body, has ``ag_running`` set, and cannot be closed at all - so the in-flight
        pulls are cancelled and awaited (:func:`asettle`) before the sources are closed
        (:func:`aclosing_all`).  Cancelling is the only way to reach a source suspended
        inside its own body; ``aclose()`` is the only way to reach one parked at its
        ``yield``, whose item was just handed to the consumer and which has no pull to
        cancel.  Neither phase reaches the other's sources, which is why doing only one
        of them leaves generators unclosed.

        Whatever a source raises from its own cleanup is absorbed here rather than
        re-raised: by then the merge is unwinding and there is no consumer left to
        receive it.  See :func:`asettle` for why that is where such a failure lands, and
        :func:`~turbopipes.aselect` for a merge that reports those failures to the event
        loop's exception handler rather than dropping them.

    Example::

        async def main() -> None:
            stream = amerge([read_clicks(), read_ticks()])
            async with contextlib.aclosing(stream):
                async for item in stream:
                    print(item)
    """
    gens = [*gens]
    pulls: dict[asyncio.Task[_T], AsyncGenerator[_T, None]] = {}
    ready: list[tuple[AsyncGenerator[_T, None], asyncio.Task[_T]]] = []

    def arm_pull(gen: AsyncGenerator[_T, None]) -> None:
        pulls[asyncio.create_task(pull(gen))] = gen

    async with aclosing_all(gens):
        try:
            for gen in gens:
                arm_pull(gen)

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
                    done_pulls = [task for task in pulls if task in done]
                    for task in done_pulls:
                        ready.append((pulls.pop(task), task))

                gen, task = ready.pop(0)
                if is_exhausted(task):
                    continue  # the source is spent; don't re-arm it

                # `result()` rather than `await`: the pull is already complete, and a
                # source's failure is meant to surface here as the merge's own.
                yield task.result()
                arm_pull(gen)  # only now that the consumer has come back for more
        finally:
            # Settled before the sources are closed - which the exit stack above
            # guarantees by construction, rather than by the order of statements here.
            # Completed-but-unyielded pulls are settled too, so that their results and
            # exceptions are retrieved rather than orphaned.
            await asettle([*pulls, *(task for _, task in ready)])
