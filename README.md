# `turbopipes`: Composable async generator pipelines (Python)

`turbopipes` provides a variety of general purpose pipe-like building blocks, aimed at unleashing the power of Python's async generators, with a focus on safety and reliability.

Async generators are extremely powerful in Python, but can often become very verbose, and tough to implement correctly - particularly when it comes to concurrency, error handling, and garbage collection/cleanup.

`turbopipes` smoothes out such issues with composable building blocks - essentially digital drain cleaner for your async pipes!

## Features

- **Composable Generators**: Feed an async generator in, get a transformed async generator out.
    - Writing complex data processing workflows with async generators can result in verbose and hard-to-read code.
    - Manually chaining async generators requires boilerplate code and meticulous management of the generator states.
    - `turbopipes` allows you to compose smaller, reusable async generator functions into larger workflows, reducing boilerplate and enhancing readability.

- **Type Safety**: Maintain type safety throughout the pipeline construction.
    - Without type safety, it's easy to introduce bugs by passing incorrect types between async generator stages.
    - Ensuring the correct types at each stage manually can be error-prone and cumbersome.
    - `turbopipes` leverages Python's type hints to preserve type information, catching errors at the pipeline construction phase and ensuring data integrity.

- **Consistent Cancellation**: Simplify the complexity of considering error handling edge cases, with consistent cancellation.
    - Handling cancellation and cleanup in async code can be tricky, often leading to resource leaks or incomplete cleanups.
    - Manually managing cancellations across multiple async generators requires careful coordination and error handling.
    - `turbopipes` abstracts cancellation management, providing consistent behavior and reducing the risk of resource leaks or dangling tasks.

- **Proper Cleanup**: Take the guesswork out of choosing when/how to close async generators by following general patterns.
    - Forgetting to close async generators properly can lead to resource leaks and unexpected behavior.
    - Ensuring all generators are correctly closed, especially in the presence of exceptions, adds complexity to your code.
    - `turbopipes` follows established cleanup patterns, automatically managing the lifecycle of async generators to ensure proper cleanup.

- **Concurrency**: Do all of the above concurrently, making full use of the benefits of async.
    - Async generators often end up being processed sequentially, negating the benefits of asynchronous execution.
    - Implementing concurrent processing manually with `asyncio.Queue` and managing multiple producers and consumers can become complex and error-prone.
    - `turbopipes` provides concurrency control primitives like `aparallel`, which parallelizes async generator processing without the need for manual queue management.
    - Ensuring correct shutdown and error reporting in complex concurrency scenarios is simplified with `turbopipes`.

- _(future)_ **Composable Pipelines**: Easily chain together asynchronous generators using the `|` operator.
    - Constructing complex data processing pipelines with async generators can be verbose and prone to errors without proper abstractions.
    - Manually chaining async generators often results in hard-to-read and difficult-to-maintain code.
    - The `|` operator simplifies the chaining of async generators, making pipelines more readable and maintainable by providing a clear and concise syntax for pipeline construction.

## Usage

This is a brief overview of `turbopipes` usage. Refer to the docstrings for more detailed information.

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

Plus it's possible that in the future, some of the building blocks my have non-async variants, and having separate names can avoid pitfalls with otherwise excessively clever type inference of using the same name for both variants.
