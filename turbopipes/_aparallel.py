import asyncio
import asyncio.events
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import Awaitable
from collections.abc import Coroutine
from typing import TypeVar

T_co = TypeVar('T_co', covariant=True)


async def aparallel(
    gen: AsyncGenerator[Awaitable[T_co], None],
    max_concurrent: int,
) -> AsyncGenerator[asyncio.Task[T_co], None]:
    """Wraps an async generator to process multiple items concurrently.

    The input generator yields futures to be processed in parallel, and the resulting
    wrapped generator consumes multiple futures at once, maintaining a rolling window of
    tasks being processed in parallel, up to ``max_concurrent`` at a time.

    Rather than yielding completed ``T_co`` items, ``asyncio.Task[T_co]`` tasks are
    yielded.  This may seem counterintuitive in some sense, but it has the following
    advantages:

        * The input generator and parallelized generator both yield awaitables,
          symmetrically.
        * The consumer has more control over handling of failed tasks.  If a task
          raises an exception, then it's not until the consumer actually awaits the
          task that the exception is surfaced, at which point the consumer can choose
          to handle the exception gracefully, or let it bubble up, in which case the
          entire task group is cancelled in a consistent manner.

    Note:
        The completed futures are yielded as they are completed, rather than the order
        in which they're submitted by the input generator.  Although this means that
        completed tasks may come out in a different order than how they came in, it
        means that if one task is much slower than others, the worker pool can continue
        making progress on subsequent items, and the consumer gets the results as soon
        as possible rather than being bottlenecked by the slower item(s).

    Warning:
        The caller is responsible for closing this async generator in the event of
        prematurely halting consumption, in order to avoid memory leaks.  Consider the
        case for example where the caller handles a completed future, which surfaces an
        exception.  The caller breaks out of its ``async for`` loop and ceases to
        consume the generator, leaving it running in the background until it's
        explicitly closed (e.g. via :meth:`contextlib.aclosing`).  Although the
        generator will not pull any additional items from the underlying generator, it
        still retains references to any unacknowledged tasks until exhausted or closed.

        This consideration is not specific to :meth:`parallelize`, but rather in Python
        asyncio in general with async generator cleanup (unlike non-async generator
        cleanup, which can sloppily rely on the garbage collector to eventually shut
        down the generator, rather than asyncio only closing orphaned async generators
        at the very end of the event loop).

        Keep in mind that when the :meth:`parallelize` async generator is closed,
        cancellation of any active tasks is abrupt.

    Example::

        async def do_work(index: int) -> int:
            await asyncio.sleep(random.random())
            return index * 2

        async def generate_work() -> AsyncGenerator[Awaitable[int], None]:
            for index in range(50):
                yield do_work(index)

        async def main() -> None:
            work_generator = parallelize(generate_work(), 10)
            async with contextlib.aclosing(work_generator):
                async for task in work_generator:
                    print(task.result())
    """

    async def to_coro(item: Awaitable[T_co]) -> T_co:
        return await item

    async def await_task(task: asyncio.Task[T_co]) -> None:
        """Propagates cancellation downward, but swallows domain exceptions upward."""
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            task.cancel()
            raise

    # TBD: one could argue that this should *not* take ownership of the user-supplied
    # `gen` - i.e. leave it to the caller to do `aclosing`.

    async with asyncio.TaskGroup() as group, contextlib.aclosing(gen):
        pending_inner_tasks: set[asyncio.Task[T_co]] = set()

        async for item in gen:
            coro = item if isinstance(item, Coroutine) else to_coro(item)
            inner_task = asyncio.create_task(coro)
            group.create_task(await_task(inner_task))
            pending_inner_tasks.add(inner_task)

            if len(pending_inner_tasks) >= max_concurrent:
                done_tasks, pending_inner_tasks = await asyncio.wait(
                    pending_inner_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for done_task in done_tasks:
                    yield done_task

        # Drain phase
        for pending_task in pending_inner_tasks:
            yield pending_task
