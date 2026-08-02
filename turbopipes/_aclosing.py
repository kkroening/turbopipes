import asyncio
import contextlib
from ._pulling import is_exhausted
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Iterable
from typing import Any
from typing import TypeVar

_T = TypeVar('_T')


def _report_cleanup_failure(label: object, task: asyncio.Task[Any]) -> None:
    """Reports a source's own cleanup failure, if its cancelled pull carries one.

    A source that unwinds on the cancellation of its in-flight pull raises anything from
    its own ``finally`` onto that cancelled pull rather than onto any caller: by then
    the surrounding pipeline is already unwinding and there's nobody left to raise it
    to.  Handing it to :meth:`asyncio.loop.call_exception_handler` - asyncio's own route
    for an exception that nobody can receive - keeps the failure visible without
    re-raising it here, where it would only displace the ``GeneratorExit`` or
    ``CancelledError`` that's doing the unwinding.

    A cancelled pull usually carries no such failure: the source may have propagated
    the cancellation; or caught it and returned, ending its own iteration, so that the
    pull raises ``StopAsyncIteration`` rather than cancelling; or swallowed it and
    yielded once more, leaving the pull with an ordinary value.  Only an exception
    raised while unwinding is a cleanup failure - and not even then if it's spelled
    ``CancelledError``: a source raising one of those afresh out of its own ``finally``
    leaves the pull cancelled, indistinguishable from the source having propagated the
    cancellation it was sent, so it goes unreported rather than guessed at.

    A pull that had already *completed* before the settling reached it isn't one
    either - it carries whatever the source produced or raised on its own account - but
    that case never arrives here, because only the pulls that were actually cancelled
    are handed over.

    ``KeyboardInterrupt`` and ``SystemExit`` never arrive here at all, and it isn't the
    ``return_exceptions=True`` gather that keeps them out: :class:`asyncio.Task`'s step
    handler re-raises those two types specifically into the event loop after storing
    them, so the run comes apart before the settling reaches this reporting.  That's
    worth knowing before touching the gather, which isn't the thing drawing that line -
    it stores a ``BaseException`` as readily as an ``Exception``, and what's reported is
    decided by the predicate below.  A ``BaseException`` is therefore swallowed here
    like any other, since reporting deliberately doesn't re-raise, and that's the
    intended outcome for a cleanup failure whatever it derives from.
    """
    exc = None if task.cancelled() or is_exhausted(task) else task.exception()
    if exc is not None:
        source = 'a source' if label is None else f'source {label!r}'
        asyncio.get_running_loop().call_exception_handler(
            {
                'message': (
                    f'{source} raised during its own cleanup while the surrounding '
                    f'pipeline was being torn down; there was no consumer left to '
                    f'raise it to'
                ),
                'exception': exc,
                'task': task,
            }
        )


async def asettle(
    tasks: Iterable[asyncio.Task[Any]],
    *,
    label: object = None,
) -> None:
    """Cancels the given tasks and waits for every one of them to finish.

    This is the half of an async generator's cleanup that has to happen *before*
    :meth:`~agen.aclose`, whenever the generator might be part-way through serving an
    ``__anext__()``.  Such a generator is suspended at an ``await`` inside its own body
    and has ``ag_running`` set, and closing one in that state raises::

        RuntimeError: aclose(): asynchronous generator is already running

    Cancelling the pull is the only way to reach it: the generator can't be closed until
    it has left that ``await``, and cancelling is what makes it leave.

    Tasks that have *already* completed are awaited rather than cancelled, so that
    whatever they hold - a result, or an exception nobody consumed - is retrieved rather
    than orphaned.  Nothing is raised on their account: every outcome is absorbed, since
    this runs on a path that is already unwinding and has no consumer left to raise to.

    A source that raises from its own ``finally`` in response to the cancellation is the
    one outcome that doesn't simply vanish: it's reported to the event loop's exception
    handler, which is asyncio's route for an exception nobody can receive.  ``label``
    names the offending source in that report; without one the report still happens, and
    just doesn't say which source it was.  Only pulls that were *actually cancelled*
    here are eligible - a pull that had already completed carries an ordinary result or
    failure that a consumer merely walked away from, and reporting that as a cleanup
    failure would be a false log line.
    """
    tasks = [*tasks]
    cancelled = [task for task in tasks if not task.done()]
    for task in cancelled:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for task in cancelled:
        _report_cleanup_failure(label, task)


@contextlib.asynccontextmanager
async def aclosing_all(
    gens: Iterable[AsyncGenerator[_T, None]],
) -> AsyncIterator[None]:
    """Closes every one of ``gens`` on the way out, however the block is left.

    The bulk counterpart to :func:`contextlib.aclosing`, for the case where ownership of
    several generators is taken at once - a merge over its sources, most obviously.
    Every generator is closed even if closing an earlier one raises, which a stack of
    nested ``aclosing`` blocks would not do: the first failure there skips the rest.

    Note:
        Only the *last* exception raised by a close propagates; the others are
        discarded rather than chained onto it.  That is :class:`contextlib.AsyncExitStack`'s
        behaviour and it's kept deliberately, since the alternative - collecting them
        into an exception group - would change the type a caller sees depending on how
        many of its generators happened to misbehave.

    Warning:
        A generator part-way through an ``__anext__()`` cannot be closed at all; see
        :func:`asettle`, which is what makes it closeable.  The two are used together,
        and the settling has to happen *inside* this block, so that it runs before the
        closes rather than after them::

            async with aclosing_all(gens):
                try:
                    ...
                finally:
                    await asettle(pulls)

        Nesting them the other way around closes the generators first and settles the
        pulls afterwards, which is the mistake this looks like it saves you from.
        Putting the closes on an exit stack rather than in a ``finally`` is what makes
        that ordering structural: it holds even if the block is interrupted part-way
        through its own cleanup.
    """
    async with contextlib.AsyncExitStack() as stack:
        for gen in gens:
            stack.push_async_callback(gen.aclose)
        yield
