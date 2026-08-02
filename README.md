# `turbopipes`: Bulletproof Aysnc Generator Pipelines (Python)

[![CI][ci-badge]][ci]
[![Changelog][changelog-badge]][changelog]

[changelog-badge]: https://img.shields.io/badge/Changelog-%20-%23
[changelog]: ./CHANGELOG.md
[ci-badge]: https://github.com/kkroening/turbopipes/actions/workflows/ci.yml/badge.svg
[ci]: https://github.com/kkroening/turbopipes/actions/workflows/ci.yml

Async generators are incredibly powerful in Python, but mixing them with concurrency is notoriously
difficult to get _right_.

If you've ever tried to write a concurrent async pipeline, you've likely relied on naive approaches
like batching with `asyncio.gather()` (which bottlenecks on the slowest task in the chunk) or
manually wiring up `asyncio.Queue` worker pools (which often leads to dangling tasks and memory
leaks during shutdown).

`turbopipes` provides composable, reliable building blocks to parallelize async generators. It
enforces strict concurrency limits, maintains crucial backpressure, and ensures bulletproof garbage
collection—so you don't have to.

## ⚡ Quick & Dirty: The Happy Path

At its simplest, `turbopipes` is highly approachable. If you just want to take a stream of tasks and
run them concurrently with a rolling window, it takes three lines of code.

```python
import asyncio
import turbopipes

async def main():
    # 1. A generator yielding un-awaited coroutines (your tasks)
    gen = (my_async_work(i) for i in range(100))

    # 2. Wrap it to maintain exactly 10 concurrent tasks at all times
    pipeline = turbopipes.aparallel(gen, max_concurrent=10)

    # 3. Consume the results as soon as they finish
    async for done_task in pipeline:
        result = await done_task
        print(f'Finished: {result}')

asyncio.run(main())
```

_(Disclaimer: This is the quick-and-dirty method. It works great for simple scripts and is already
more efficient than asyncio.gather chunks, but for production systems, see the Bulletproof section below)_.

## 🛡️ The Bulletproof Approach (Production Ready)

In the real world, the "happy path" rarely stays happy. What happens if the source generator explodes while tasks are inflight? What if an individual task fails? What if the downstream consumer gets an exception, or the application receives a `SIGINT`?

To handle all of these vectors safely, `turbopipes` is designed to be paired with `contextlib.aclosing`.

```python
import asyncio
import contextlib
import turbopipes

async def main():
    gen = (my_async_work(i) for i in range(100))
    pipeline = turbopipes.aparallel(gen, max_concurrent=10)

    # Guarantee safe teardown and cancellation of inflight tasks
    async with contextlib.aclosing(pipeline):

        async for done_task in pipeline:
            try:
                # `aparallel` yields awaitables, giving YOU control over the exception
                result = await done_task
                print(f'Finished: {result}')
            except Exception as exc:
                print(f'A specific task failed, but the pipeline survives: {exc}')
```

## 🔀 Merging Several Sources: `aselect`

Sometimes the problem isn't fanning one stream _out_ across workers, but fanning several streams
_in_. `aselect` merges a mapping of async generators into a single stream, handing you each item as
soon as whichever source produced it, tagged with that source's key so you know who spoke.

```python
import contextlib
import turbopipes

async def main():
    sources = {'clicks': read_clicks(), 'ticks': read_ticks()}
    stream = turbopipes.aselect(sources)

    async with contextlib.aclosing(stream):
        async for key, task in stream:
            try:
                print(f'{key}: {await task}')
            except Exception as exc:
                print(f'{key} failed, but the other sources keep going: {exc}')
```

Same bargain as `aparallel`: you get an awaitable rather than a bare item, so a single misbehaving
source can't tear the merge down behind your back. Backpressure is maintained per source—at most one
pull is in flight for each of them, and none is re-armed until your loop comes back for another
item—so a chatty source can't run away from a slow consumer. A source that runs dry drops out
quietly; the merge itself ends when the last one does.

The teardown is the part worth knowing about. When you walk away early, sources can be left
suspended mid-`__anext__()`, and an async generator suspended _inside its own body_ cannot be
closed—`aclose()` raises `RuntimeError: aclose(): asynchronous generator is already running`, right
out of the cleanup path, masking whatever cancellation was in progress and leaving every one of
those sources unclosed. So every in-flight pull is cancelled and waited for _before_ any source is
closed—by the `amerge` and `ataskify` layers underneath `aselect` (see _The Pieces Underneath_,
below), each of which settles the pulls it armed itself. This is the one corner of the library where
`aclosing` alone wouldn't have been enough: `aselect` takes ownership of the sources you hand it, and
closes every one of them for you.

That ownership begins when the merge does, which is worth knowing and is a property of async
generators rather than of `aselect` in particular. `aselect` hands you back an `amerge` generator,
and an async generator runs none of its body—including the part that arranges those closes—until you
first advance it. A merge that gets closed without ever having been advanced (an early `return`
before the `async for`, say) leaves its sources untouched, and they're still yours to close at that
point.

One more consequence of that ownership, worth knowing before you run a merge inside a `TaskGroup` or
under a timeout: a source's _own_ cleanup failure surfaces differently depending on how that source
was torn down—and doesn't always surface at all. A source that gets closed propagates it out to
whoever closed the merge, which is what you want when you closed the merge deliberately—but it also
means that a merge being _cancelled_ surfaces that failure in place of the `CancelledError`, so a
cancelled consumer can look like it raised, and a `TimeoutError` can go missing. Only one such
failure can propagate, though: every source still gets closed, but only the last failure raised
survives, so if several sources fail their own cleanup the rest are dropped rather than chained onto
it. A source that unwinds on its cancelled pull instead has nobody left to raise to, so its failure
goes to the event loop's exception handler—logged, not propagated. That route is where the "doesn't
always surface" above comes from: if what the source raised is itself a `CancelledError`, its pull
looks exactly like one whose source simply propagated the cancellation it was sent, so nothing is
reported at all—and a `finally` that merely awaits something already cancelled is enough to land
there.

## 🧱 The Pieces Underneath: `ataskify`, `atag`, `amerge`

`aselect` isn't primitive—it's sugar, and thin enough to be worth reading:

```python
turbopipes.amerge([turbopipes.atag(key, turbopipes.ataskify(gen, label=key))
                   for key, gen in sources.items()])
```

Three separate jobs, and you can take any one of them on its own:

- **`ataskify(gen)`** turns a value-yielding generator into a task-yielding one. This is where "a bad
  source can't tear the merge down" actually comes from—the failure becomes a task that raises when
  _you_ await it, instead of an exception thrown out of the iteration.
- **`atag(key, gen)`** staples a constant key onto whatever a generator yields. It doesn't know or
  care what that is.
- **`amerge(gens)`** interleaves a _sequence_ of generators in completion order, and knows nothing
  about keys or tasks. On its own it's the blunt version: if one source raises, the whole merge comes
  down and takes its peers with it—exactly like an `async for` over a single generator that raises.
  That's the behaviour you get when you _don't_ wrap your sources in `ataskify` first.

The order matters, and the tempting order is the wrong one. Tag the **taskified** generator, not the
source. Tag the source directly and the key ends up _inside_ the task, where reading it means
awaiting—and by then the pull has already happened. Deciding _how_ to await based on _which_ source
it is (the whole reason you wanted keys) is no longer possible. It only bites in the failure case,
which is where it hurts most.

So reach past `aselect` whenever it doesn't fit: drop `atag` if your events already say who they are,
drop `ataskify` if one source failing genuinely _should_ end everything, or keep both and get your
keys from somewhere other than a mapping.

### Cleanup, in two pieces

Two more exports, both extracted from the teardown described above, because getting it right in one
place and importing it beats getting it right in four:

- **`aclosing_all(gens)`** is bulk `contextlib.aclosing`, and what it buys you is _dynamic arity_.
  Nesting `aclosing` blocks means writing one `async with` per generator, which you can't do over a
  sequence whose length you only learn at runtime—so you reach for `AsyncExitStack`, and this is that
  packaged. Every generator gets closed even if closing an earlier one raises, but so does a nested
  stack; the construction that _doesn't_ is the obvious `for gen in gens: await gen.aclose()`, where
  the first failure abandons the rest.
- **`asettle(tasks)`** cancels in-flight pulls and waits for them to land, which is the step that
  makes a mid-`__anext__()` generator closeable at all.

They pair, and the nesting is not optional—settle _inside_, so it happens before the closes:

```python
async with turbopipes.aclosing_all(gens):
    try:
        ...
    finally:
        await turbopipes.asettle(pulls)
```

Do it the other way round and you're closing generators that are still running, which is the
`RuntimeError` this whole section exists to avoid.

## FAQ

### Why is the interface designed this way?

To the untrained eye, the turbopipes API might seem arbitrary. Why does it require an async
generator instead of a standard iterable? Why does aparallel yield awaitables rather than the
direct results? Why is aclosing so important?

We promise, every design choice is there to protect you from the fundamental complexities of async
pipelines:

-   **Backpressure**: Taking an async generator as input ensures we don't pull data from the source
    faster than your downstream loop can consume it.
-   **Awaiting the Yield**: By yielding awaitables, `turbopipes` prevents a single failed
    background task from silently tearing down your entire pipeline before you are ready to handle
    the exception. You decide how to handle the try/except.
-   **Complete Cancellation**: Wrapping the pipeline in `aclosing` guarantees that whether your
    application crashes, a downstream consumer fails, or the OS sends a kill signal, all pending
    concurrent tasks are safely cancelled, preventing silent memory leaks.

**📖 Want to understand the "Why"?** Read our deep-dive article, which walks through the pitfalls
of the two async pipelines everyone writes first — batching with `asyncio.gather()`, hand-rolled
`asyncio.Queue` worker pools — and derives the `turbopipes` architecture from first principles,
through to why `aselect` has to cancel its in-flight pulls before it closes anything. Every
measurement it quotes ships with the script that produced it:

#### [👉 Read: Deriving Turbopipes from First Principles (Why Async is Harder Than It Looks)](./doc/first-principles.md)

### Why are the functions prefixed with `a`? (e.g. `aparallel` vs `parallel`)

A common convention in the land of Python async is to distinguish the async generator/iterator variants of methods with `a`, such as `aclose` vs `close`, `contextlib.aclosing` vs `contextlib.closing`, etc.

Plus it's possible that in the future, some of the building blocks may have non-async variants, and having separate names can avoid pitfalls with otherwise excessively clever type inference of using the same name for both variants.
