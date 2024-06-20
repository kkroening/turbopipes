# `turbopipes`: Composable async generator pipelines (Python)

`turbopipes` provides a variety of general purpose pipe-like building blocks, aimed at unleashing the power of Python's async generators, with a focus on safety and reliability.

Async generators are extremely powerful in Python, but can often become very verbose, and tough to implement correctly - particularly when it comes to concurrency, error handling, and garbage collection/cleanup.

`turbopipes` smoothes out such issues with composable building blocks - essentially digital drain cleaner for your async pipes!

## Features

- **Composable Generators**: Feed an async generator in, get a transformed async generator out.
- **Type Safety**: Maintain type safety throughout the pipeline construction.
- **Consistent Cancellation**: Simplify the complexity of considering error handling edge cases, with consistent cancellation.
- **Proper Cleanup**: Take the guesswork out of choosing when/how to close async generators by following general patterns.
- **Concurrency**: Do all of the above concurrently, making full use of the benefits of async.
- _(future)_ **Composable Pipelines**: Easily chain together asynchronous generators using the `|` operator.

## Usage

> [!NOTE]
> This is only a brief overview of `turbopipes`. Refer to the docstrings for more info.

### `aparallel`

The `aparallel` function allows you to process multiple items concurrently from an async generator, maintaining a rolling window of tasks being processed in parallel.

#### Example

```python
from contextlib import contextlib
import turbopipes
import asyncio
from collections.abc import AsyncGenerator
from collections.abc import Awaitable


class SomeNonCriticalError(Exception):
    pass


async def do_work(item_num: int) -> int:
    print('Begin item', item_num)
    await asyncio.sleep(0.2)

    if item_num == 5:
        raise SomeNonCriticalError('Womp womp...')

    # if item_num == 6:  # uncomment to simulate fatal task error
    #     raise RuntimeError('Uhoh!')

    print('Finish item', item_num)
    return item_num * 2


async def generate_work() -> AsyncGenerator[Awaitable[int], None]:
    for item_num in range(10):
        # Kick off coroutines (or asyncio tasks) but don't `await` yet.  `aparallel`
        # will pull this stream of awaitables and then start the coroutines/tasks as
        # needed, to ensure that up to `max_concurrent` tasks run concurrently:
        yield do_work(item_num)


async def main() -> None:
    # Kick off a work generator, and compose it with `aparallel` to do it concurrently:
    gen = generate_work()
    gen = turbopipes.aparallel(gen, max_concurrent=3)

    async with contextlib.aclosing(gen):  # best practice for consistent shutdown
        # Consume tasks as soon as they finish:
        async for done_task in gen:
            try:
                item_num = await done_task
                print('Consumed item:', item_num)

                # Simulate backpressure; pending tasks continue executing concurrently:
                await asyncio.sleep(0.1)

            except SomeNonCriticalError as error:
                # Task exceptions can be handled gracefully if desired:
                pass

            except Exception:
                # Unhandled/re-raised exceptions bubble up and cancel the pipeline:
                raise


asyncio.run(main())
```

## FAQ

#### Why are the functions prefixed with `a`? (e.g. `aparallel` vs `parallel`)

A common convention in the land of Python async is to distinguish the async generator/iterator variants of methods with `a`, such as `aclose` vs `close`, `contextlib.aclosing` vs `contextlib.closing`, etc.

Plus it's possible that in the future, some of the building blocks may have non-async variants, and having separate names can avoid pitfalls with otherwise excessively clever type inference of using the same name for both variants.
