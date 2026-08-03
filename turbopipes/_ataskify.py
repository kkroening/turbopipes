import asyncio
import contextlib
from ._aclosing import asettle
from ._pulling import is_exhausted
from ._pulling import pull
from collections.abc import AsyncGenerator
from typing import TypeVar

_T = TypeVar('_T')


async def ataskify(
    gen: AsyncGenerator[_T, None],
    *,
    label: object = None,
) -> AsyncGenerator[asyncio.Task[_T], None]:
    """Wraps a value-yielding async generator to yield its pulls as tasks instead.

    Each item comes out as an :class:`asyncio.Task` that has *already completed*; it is
    the consumer's ``await`` that turns it back into a value, or raises whatever the
    source raised producing it::

        async for task in ataskify(source()):
            try:
                item = await task
            except Exception:
                ...  # this source failed, and the decision was the consumer's

    That indirection is the whole point.  A source failure reaches the consumer as a
    task that raises on await, rather than as an exception torn out of the iteration
    itself, so the consumer decides what the failure means instead of being handed a
    dead loop.  It also makes the source's readiness observable *before* its value is:
    what comes out is a completed pull, and a consumer merging several of these can see
    which source is ready and act on that before deciding how to await it.

    Note:
        Backpressure is preserved: the next pull isn't armed until the consumer comes
        back for another item, so a source can't run ahead of a slow consumer.

        Exhaustion stays a property of the iteration rather than becoming a value - a
        spent source ends this generator, and never yields a task carrying
        ``StopAsyncIteration``.

    Warning:
        The caller is responsible for closing this generator if consumption stops early
        - e.g. via :func:`contextlib.aclosing` - as with the rest of this library.

        This generator takes ownership of ``gen`` and closes it on the way out, whether
        it finishes normally, is closed early, or is cancelled.  Ownership begins when
        iteration does: like any async generator, this one runs none of its body until
        first advanced, so closing one that was never advanced leaves ``gen`` untouched
        and still the caller's to close.

        Between arming a pull and the consumer taking it, this generator is itself
        suspended at an ``await`` inside its own body - which it has to be, or a merge
        over several of these would find every one of them instantly ready and hand the
        consumer all of them at once, losing both readiness ordering and backpressure.
        A generator in that state has ``ag_running`` set and cannot be closed, so the
        in-flight pull is cancelled and awaited before ``gen`` is closed; see
        :func:`asettle`.

        A caller can tear this generator down two ways, and they differ.  Cancelling
        the task that is awaiting this generator's ``__anext__()`` gets ``gen``'s
        teardown for free: the cancellation unwinds this body, whose own cleanup
        settles and closes ``gen`` on the way out - the arrangement that lets a merge
        upstream cancel one pull and have a whole chain come apart correctly beneath
        it.  Calling ``aclose()`` while a pull is still outstanding does not, and
        raises the ``RuntimeError`` in :func:`asettle`, since this generator is itself
        mid-``await`` - exactly as for any other async generator, and the reason a
        merge settles before it closes rather than relying on ``aclose()`` alone.

        An ordinary ``async for`` under :func:`contextlib.aclosing` is never mid-pull
        when it closes - the loop only reaches the close between items, with no pull
        outstanding - so the distinction doesn't arise for the usage above.

        ``label`` is diagnostics only: it names ``gen`` if it raises from its own
        ``finally`` in response to its in-flight pull being cancelled - a failure that
        reaches no consumer and is reported to the event loop's exception handler
        instead (see :func:`asettle`).  It exists because this is the only layer
        positioned to *see* such a failure, and also the only one with no other reason
        to know what the source is called; a caller that has a name for it therefore
        has to hand that name down, or the report can't say which source it was.
    """
    armed: list[asyncio.Task[_T]] = []

    async with contextlib.aclosing(gen):
        try:
            while True:
                task = asyncio.create_task(pull(gen))
                armed.append(task)

                # Wait rather than await: the pull's outcome belongs to the consumer,
                # and awaiting it here would surface a source failure as this
                # generator's own, which is exactly the coupling being removed.
                await asyncio.wait([task])

                armed.clear()  # settled by definition; whatever it holds is now owed
                if is_exhausted(task):  # ...to the consumer, unless the source is spent
                    break

                yield task
        finally:
            # Only an *unconsumed* pull is settled here.  One already yielded belongs to
            # the consumer, who may not have awaited it - retrieving its exception on
            # their behalf would swallow a failure they were entitled to see.
            await asettle(armed, label=label)
