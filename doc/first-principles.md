# Deriving Turbopipes from First Principles

### _Why async is harder than it looks_

The `turbopipes` API looks slightly arbitrary the first time you meet it. It insists on an async
generator rather than any old iterable. It hands you `asyncio.Task` objects instead of the results
you asked for. Its documentation nags you about `contextlib.aclosing` on nearly every example.

None of that is taste. Each of those decisions is the answer to a specific way that the obvious
designs fall over — usually not on the happy path, but at the moment a consumer stops consuming.
This document walks that road: two designs everyone writes first, where each one breaks, and the
shape that's left standing afterwards.

Every measurement quoted below was produced on **CPython 3.14.6** by a script in
[`first-principles/`](./first-principles/) — one per block, each printing exactly the block it
backs, bar two whose variability is called out where they appear. If you don't believe a claim,
run it.

---

## 1. The problem

An async generator is a lazy stream. You pull an item, it does some I/O, you pull the next one:

```python
async def fetch_all(urls):
    async for url in urls:
        yield await fetch(url)
```

Beautiful, composable, and strictly sequential — one request in flight at a time. To go faster you
want several in flight at once, and that is where it stops being simple, because the moment more
than one thing is in flight you own a **set of tasks**, and you own them for as long as they exist.
Including the moment your consumer decides it has seen enough.

Three things a concurrent pipeline has to get right:

- **Throughput** — keep the concurrency window full, rather than nominally full.
- **Backpressure** — don't produce faster than the consumer consumes.
- **Teardown** — when consumption stops, stop everything, and don't lose the reason it stopped.

The next two sections are the two designs everyone writes first, running into those one at a time.

---

## 2. Attempt one: `asyncio.gather()` in chunks

The batching approach. Slice the work into chunks, `gather` each chunk, yield the results:

```python
async def chunked(items, size):
    for start in range(0, len(items), size):
        chunk = items[start:start + size]
        for result in await asyncio.gather(*(work(item) for item in chunk)):
            yield result
```

Four lines, genuinely concurrent, and perfectly adequate when every item costs about the same.
Real workloads are not like that. Real workloads have a long tail: most items are quick, and every
so often one takes a second.

Barring an exception — §2.1 gets to those — `gather` doesn't come back until its *last* member
does. So a chunk advances at the pace of its slowest member, and while that member grinds away,
the rest of the window sits idle. Twenty-four items, chunks of eight, three of them slow:

```
gather in chunks of 8          first result 1.00s | total 3.00s | mean tasks in flight 1.1 of 8
aparallel(max_concurrent=8)    first result 0.01s | total 1.02s | mean tasks in flight 3.1 of 8
```

Three times the wall clock, and the first result took a hundred times longer to show up. The
window is nominally eight wide; the occupancy figures are sampled every 5 ms across the run, so
their last digit moves a little between runs. The chunked version averages **one** task in flight,
because it spends nearly all of its time waiting on a straggler with seven slots sitting empty.

A bigger chunk isn't the fix. It raises the ceiling on concurrency, but it also widens the barrier:
the more items per chunk, the likelier one of them is a straggler, and the more peers wait behind
it.

What the measurement is really saying is that **the chunk boundary is a synchronization barrier
nobody asked for.** Take the barrier away and the design that remains is a rolling window: start a
new item whenever one finishes, and yield results in completion order rather than submission
order.

### 2.1 …and the failure behaviour is worse than the throughput

Give one item in a chunk an exception. `gather` raises it out of your `await` immediately — and
leaves the peers running:

```
asyncio.gather         peers -> ['a', 'b']
```

Both peers ran to completion, after the `gather` they belonged to had already raised. Their
results went nowhere. You are left with work still in flight that you have no handle on, running
on behalf of a chunk you have already given up on.

---

## 3. Attempt two: a hand-rolled `asyncio.Queue` worker pool

So: no barriers. The textbook answer is a queue and a pool of workers.

```python
async def naive_pool(gen, results, max_concurrent):
    queue = asyncio.Queue(maxsize=max_concurrent)

    async def feeder():
        async for item in gen:
            await queue.put(item)
        for _ in range(max_concurrent):
            await queue.put(None)          # one poison pill per worker

    async def worker():
        while True:
            item = await queue.get()
            if item is None:
                break
            await results.put(await work(item))

    return [asyncio.create_task(feeder())] + [
        asyncio.create_task(worker()) for _ in range(max_concurrent)
    ]
```

This genuinely fixes throughput. Workers pull independently, so a straggler holds up exactly one
worker instead of the whole window. On the happy path it is fine.

It looks like it fixes backpressure too, since `maxsize` bounds the queue between the feeder and
the workers, so the feeder blocks whenever the workers fall behind. But nothing bounds the queue on
the *other* side. Workers put their results into `results`, which is unbounded, so the workers
never block, so the feeder never blocks, so the source never stops. A consumer that merely
dawdles — still there, still consuming, just slowly — finds that out:

```
consumed 1, source has produced 261  (ahead by 260)
consumed 2, source has produced 511  (ahead by 509)
consumed 3, source has produced 761  (ahead by 758)
consumed 4, source has produced 1011  (ahead by 1007)
```

Fifty event-loop passes of dawdling per item, and by the fourth one the source is a thousand items
ahead. The gap grows without bound, because nothing in this design connects the rate the consumer
consumes at to the rate the source produces at. Whatever the source's items cost — memory, an open
cursor, a rate-limited API call — you are paying for a thousand of them to serve four.

That is the backpressure failure, and it happens with the consumer still present and still asking.
The teardown failure is one step further on: stop consuming altogether. Read three results and walk
away:

```
consumed 0
consumed 1
consumed 2
--- consumer walks away here ---
workers still running : 5 of 5
source finally ran    : False
work items completed since we stopped consuming: 250
```

Nobody told the workers. Nobody told the feeder. Nobody told the source generator. Fifty event-loop
passes after the consumer left, 250 further items of real work had been done on behalf of someone
who was no longer there, and the source's `finally` — where you closed the database cursor — has
not run.

The obvious retort is "well, cancel the tasks." Yes. On every exit path, including the one where
the consumer raised, which means a `try`/`finally`, which is fine — you write it. And then you
find the second half of the problem, which is the one that actually bites.

### 3.1 Async generator cleanup is not the forgiving thing you're used to

On CPython, a sync generator that goes out of scope is closed on the spot, and you have probably
been relying on that for years without noticing. An async generator is not. Same loop, same
`break`, one keyword different:

```python
def sync_source():
    try:
        for i in range(1000):
            yield i
    finally:
        log.append('sync finally')

async def async_source():
    try:
        for i in range(1000):
            yield i
    finally:
        log.append('async finally')
```

```
sync : right after the loop -> ['sync finally']
async: right after the loop -> []
async: 50 loop passes later -> ['async finally']
```

The sync generator's `finally` ran at the `break`, courtesy of refcounting. The async generator's
did not run at the `break` at all — closing an async generator means *awaiting* it, and `__del__`
cannot await. What happens instead is that asyncio installs a finalizer hook which schedules the
close for a later turn of the event loop, and `asyncio.run` sweeps up whatever is still alive at
the end via `loop.shutdown_asyncgens()`.

For a script, "some later turn of the loop" is fine. For a service that stays up for weeks, it
means the cursor stays open, the connection stays checked out of the pool, and the semaphore stays
held, for an interval you do not control and cannot observe from the code that caused it.

So the first real principle falls out, and it isn't about concurrency at all:

> **An async generator has to be closed explicitly, by someone.** That someone is the code that
> stopped consuming, since that is where the knowledge lives.

That is the entire job of `contextlib.aclosing`, and why it appears in nearly every `turbopipes`
example.

---

## 4. Deriving `aparallel`

The two attempts hand us four requirements — the three problems §1 opened with, plus the one §2.1
turned up along the way:

1. **No synchronization barrier.** Maintain a rolling window; yield in completion order.
2. **One item's failure must not take the pipeline with it** — nor silently orphan its peers.
3. **The consumer must be able to stop, and stopping must clean up** — promptly, not eventually.
4. **The producer must not run ahead of the consumer.** The gap has to be bounded by something,
   and the bound has to hold for a consumer that dawdles as well as for one that leaves.

`aparallel` is what those four look like when you write them down.

```python
pipeline = turbopipes.aparallel(gen, max_concurrent=10)

async with contextlib.aclosing(pipeline):
    async for done_task in pipeline:
        result = await done_task
```

The rolling window answers the first requirement outright. Three things about the rest of that
shape are worth deriving, since they're the three people ask about.

### 4.1 Why the input is an async generator, not a list

Because the source's own work is work, and the only thing that can bound it is the consumer not
asking for more. A list has already done that work, so there is nothing left for the consumer's
restraint to reach.

A realistic source doesn't have the items lying around; it fetches them — a page at a time, a
cursor batch at a time. Written as an async generator, producing the next item is itself an
`await`, so *not pulling* means *not doing that I/O*:

```python
async def gen():
    for page in range(20):
        for item in await fetch_page(page):     # the source's own I/O
            yield work(item)
```

```
async generator source: pages fetched before the consumer stopped: 2
materialized list      : pages fetched before the pipeline even started: 20
```

Same twenty pages of work available. The consumer took three items and left. The generator form
paid for two pages; the list form paid for all twenty before the pipeline had run a single item.
By the time you *have* a list, the argument about backpressure is already over.

That is the input's half of it. The other half belongs to the pipeline, and it is the half the
queue pool missed — its input was an async generator too, and it ran away regardless. What the
pool lacked was anything connecting consumer demand to source production: it bounded the queue
between its feeder and its workers, then left the queue between its workers and the consumer
unbounded, so the source kept running a thousand items ahead of a consumer that was still there
and merely slow. Here is §3's measurement again — a consumer dawdling fifty event-loop passes
between items, against a source that would happily produce a thousand — run against `aparallel`
instead of the queue pool:

```
consumed 1, source has produced 4  (ahead by 3)
consumed 2, source has produced 4  (ahead by 2)
consumed 3, source has produced 4  (ahead by 1)
consumed 4, source has produced 4  (ahead by 0)
```

With `max_concurrent=4` the source got four items ahead and then stopped dead, however long the
consumer dawdled. The mechanism is unglamorous: `aparallel` is itself an async generator, so
between `yield`s it isn't running, and while it isn't running it isn't pulling. That is what closes
the loop, and it is also why the input's type matters — withholding a pull only withholds work if
the work hadn't already been done.

### 4.2 Why it yields awaitables instead of results

Because "one item failed" and "the pipeline failed" are different events, and the consumer is the
one positioned to tell them apart.

Watch what three reasonable designs do to the peers of a failing item — one bad item among three:

```
asyncio.gather         peers -> ['a', 'b']
asyncio.TaskGroup      peers -> ['a CANCELLED', 'b CANCELLED']
turbopipes.aparallel   peers -> ['a', 'b']
```

`gather` lets the peers finish, but you already left via the exception, so their results are
orphaned — you did the work and threw it away. `TaskGroup` cancels them, which is exactly right
when the three tasks are one *unit of work* and wrong when they are three *rows of a stream*: one
bad row should not cancel the other 999. `aparallel` looks like `gather` in that column and isn't
the same thing at all — the peers finished *and* you were handed every task, the failing one
among them.

That is what yielding the `Task` buys. The exception doesn't happen *to* you somewhere in the
machinery; it surfaces at your own `await`, inside your own `try`, and what it means is your call:

```python
async for done_task in pipeline:
    try:
        result = await done_task
    except Exception as exc:
        log.warning('item failed, carrying on: %s', exc)
        # ...or `raise`, and the pipeline comes down with you
```

Carrying on is the interesting half of that choice; raising is the half that currently bites. On
this tree, leaving an `aparallel` loop early under the `aclosing` that §4.3 is about to insist
on — by `raise` or by `break` — does clean up, but raises on the way out, and the exception you
left with does not survive the trip:[^aparallel-teardown]

```
break, under aclosing   -> BaseExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
raise, under aclosing   -> BaseExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
  consumer's ValueError anywhere in the group, the chain, or the traceback? False
  meanwhile, the source's finally ran? True
```

That last line is the cleanup doing its job, which is why this is a defect in the teardown rather
than in the shape being derived here. It is still worth knowing before you write the `raise`,
because §5.1 is an entire section on why an exception that eats the reason it was raised makes for
a bad day, and here is the same shape arriving early.

There's a second, quieter argument for it: symmetry. The input generator yields awaitables, and
the output generator yields awaitables. `aparallel` is a transformer that preserves the shape of
what it's given, which is what lets these things stack.

### 4.3 Why `aclosing` isn't optional

This one is just §3.1 applied to the pipeline itself.

`aparallel` is an async generator like any other, and it is holding rather more than a loop
counter: a source generator it opened, and a rolling window of live tasks. Its cleanup is what
cancels those tasks and closes that source. Per §3.1, that cleanup does not run at the moment your
consumer breaks out of the loop — it runs whenever the interpreter next gets round to it.

`aclosing` is how you make "whenever" be "now". It isn't ceremony, and it isn't defensive
programming against an unlikely case: early exit is the *normal* case, since it's what `break`
does, what an exception does, and what a cancelled request does. It is also, today, the path that
trips the teardown defect §4.2 measured — the advice stands, and the bug is on the library's side
of that line rather than yours.

Nor can the library take this one off your hands, for the reason §3.1 gave: closing an async
generator has to be *awaited*, and the only code in a position to await it is the code that
stopped consuming.

---

## 5. Deriving `aselect` — the mirror-image problem

`aparallel` fans one stream **out** across many tasks. The other half of the problem is fanning
many streams **in**: several pollers, several subscriptions, several queues, one loop that wants
whichever of them speaks next.

The design is nearly forced, and it's a good one. Keep one pull in flight per source, wait for
whichever finishes first, yield it, re-arm that source:

```python
async def merge(sources):
    pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
    while pulls:
        done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            key = pulls.pop(task)
            try:
                item = task.result()
            except StopAsyncIteration:
                continue                                   # this source is spent
            yield key, item
            pulls[asyncio.create_task(anext(sources[key]))] = key
```

Now close it, the way §3.1 taught you — `aclosing` over every source, so nothing leaks. The merge
owns the sources it was handed, so the stack goes inside the generator, wrapped around the merge
loop; closing the merge is then what closes the sources:

```python
async def merge(sources):
    async with contextlib.AsyncExitStack() as stack:
        for gen in sources.values():
            await stack.enter_async_context(contextlib.aclosing(gen))
        ...
```

And then a consumer walks away while two of the three sources are mid-pull:

```
got chatty chatty0
teardown raised: RuntimeError: aclose(): asynchronous generator is already running
sources whose finally ran: ['chatty']
frames still live        : ['quiet1', 'quiet2']
```

This is where merging stops being a variation on §4 and becomes its own problem, and the
difference is worth being precise about. It isn't that one arrangement survives and the other has
to be thrown out — the arrangement survives in both cases. What differs is what each one still
needs. For `aparallel`, the teardown is correct and only its exception propagation is broken: the
defect §4.2 measured cancels the tasks and closes the source exactly as it should, and then raises
on the way out. For a merge, the teardown is *incomplete*. `aclosing` over the sources is not
sufficient by itself, and no amount of care in the closing makes it so, because a source suspended
mid-pull cannot be closed at all until something else reaches it first — which is the next section.

### 5.1 `aclose()` will not touch a generator that's inside its own body

A source part-way through serving an `__anext__()` is suspended at an `await` **inside its own
body** — waiting on a socket, a queue, an event — rather than parked at a `yield`. Consumption can
stop while it is sitting there, and that is the state `aclose()` refuses:

```python
async def src():
    try:
        await asyncio.Event().wait()    # suspended inside the body; nothing ever sets it
        yield 'unreachable'
    finally:
        print('  src finally ran')

async def main():
    gen = src()
    pull = asyncio.create_task(anext(gen))
    await asyncio.sleep(0)
    print('ag_running:', gen.ag_running, ' ag_frame is None:', gen.ag_frame is None)
    try:
        await gen.aclose()
    except RuntimeError as exc:
        print(f'aclose() raised: {type(exc).__name__}: {exc}')
    pull.cancel()
    await asyncio.gather(pull, return_exceptions=True)
```

```
ag_running: True  ag_frame is None: False
aclose() raised: RuntimeError: aclose(): asynchronous generator is already running
  src finally ran
```

Worth sitting with, since it inverts §3.1's lesson. And note *where* it raises: out of the cleanup
path itself. Here is the same merge, torn down because the consumer raised a `ValueError`:

```
the caller sees: RuntimeError: aclose(): asynchronous generator is already running
chain          : RuntimeError: aclose(): asynchronous generator is already running
                 RuntimeError: aclose(): asynchronous generator is already running
                 GeneratorExit: 
the consumer's ValueError anywhere in it: False
```

One `RuntimeError` per source that was mid-pull — two of the three, here — and then the
`GeneratorExit` that `aclose()` threw in, which has no context of its own. The chain ends there,
and the consumer's `ValueError` is gone: replaced, on its way out, by a complaint about generator
state raised from inside the cleanup that was supposed to be handling it. A teardown bug that eats
the diagnosis of the bug that triggered the teardown is a genuinely bad day, and in the merge above
it is one `break` away.

### 5.2 What does reach a running generator: cancellation

Cancel its pull. The `CancelledError` is delivered at the `await` inside the generator's body,
which is an entirely ordinary place to receive one — and unless the source catches it and carries
on, the generator unwinds:

```
after cancel: log = ['finally ran']
after cancel: ag_frame is None = True | ag_running = False
later aclose(): returned; log = ['finally ran'] (finally did not run twice)
```

Note the third line. For a source that lets the cancellation through, cancelling its pull is not
half a teardown that `aclose()` still has to finish — it is a **complete** one. The `finally` ran,
the frame is gone, and a later `aclose()` finds nothing to do.

So the teardown is two phases, in this order:

1. **Cancel every in-flight pull, and await it.** The awaiting is not a formality — it is what
   gives each source's `finally` a chance to run before anything else happens.
2. **Close every source.**

Both phases earn their keep, and it's easy to talk yourself out of either one:

- Cancelling is what reaches a source suspended inside its own body, which can't be closed until
  it has left that `await` — phase 2 is what raised.
- A source parked at its `yield` — one whose item was just handed to you, or is queued to be — is
  cleaned up by `aclose()` rather than by the cancellation.

The two do overlap, and the overlap is harmless: a source that phase 1 already tore down has
nothing left for `aclose()` to do. Which is why in
[`_aselect.py`](../turbopipes/_aselect.py) the ordering is structural rather than a matter of
statement order: the `AsyncExitStack` holding the source closes is nested *around* the
`try`/`finally` that cancels the pulls, so phase 2 cannot start until phase 1 has finished — and
still happens if phase 1 is itself interrupted.

Same scenario as above — three sources, two of them mid-pull, consumer walks away — with both
phases in place:

```
hand-rolled 2-phase    teardown: quiet
  finally ran for: ['chatty', 'quiet1', 'quiet2'] | frames live: none
turbopipes.aselect     teardown: quiet
  finally ran for: ['chatty', 'quiet1', 'quiet2'] | frames live: none
```

### 5.3 An aside: `AsyncExitStack` runs every callback; it doesn't collect their failures

Since we're leaning on `AsyncExitStack` to hold the closes, it's worth knowing precisely what it
promises when the closes themselves fail — this one surprises people, and it cuts both ways.

The good half: a callback that raises does **not** abandon the remaining ones. The stack keeps
going, so a closeable source still closes even when it sits behind two failures. That is what
makes the structural ordering above safe rather than merely tidy.

The other half. Three sources parked at a `yield`, each with a `finally` that blows up:

```python
async def parked(name):
    try:
        while True:
            yield f'{name}-item'
    finally:
        raise CleanupError(f'{name} cleanup blew up')
```

```
escaped   : CleanupError: p1 cleanup blew up
full chain: ['CleanupError: p1 cleanup blew up', 'GeneratorExit: ']
all closed: [True, True, True]
```

All three closed. **One** failure came out. The other two are not suppressed and not chained onto
it — they are gone, and nothing anywhere says so.

The mechanism is a few lines of `contextlib`:

```python
def _fix_exception_context(new_exc, old_exc):
    while 1:
        exc_context = new_exc.__context__
        if exc_context is None or exc_context is old_exc:
            # Context is already set correctly (see issue 20317)
            return
        ...
```

The stack *repairs* an exception chain that already exists; it does not build one. It walks the new
exception's `__context__` looking for the place to splice the old one on, and gives up the moment
it reaches a `None`. Here it reaches one immediately: the escaping `CleanupError` was raised from a
`finally` running under the `GeneratorExit` that `aclose()` threw in, so its `__context__` is that
`GeneratorExit` — whose own context is `None`. Walk over, nothing spliced, earlier failure gone.

The transferable lesson, for any teardown that closes several things: *"every cleanup ran"* and
*"you saw every cleanup failure"* are different guarantees, and a stack gives you the first one.

### 5.4 Exhaustion doesn't need a sentinel

A merge needs to hear that a source has run out, so that it can stop re-arming that one without
ending the whole stream. The obvious way to carry that news is a sentinel value — and a sentinel
is contagious: it widens the public type of everything downstream to
`asyncio.Task[T | type[_Exhausted]]` and puts an `isinstance` check in the consumer's loop.

No sentinel is needed here, courtesy of PEP 525:

```python
async def src():
    raise StopAsyncIteration('from the body')
    yield
```

```
RuntimeError: async generator raised StopAsyncIteration
  __cause__: StopAsyncIteration('from the body')
```

A source *cannot* hand you a `StopAsyncIteration` of its own; the interpreter converts it. So a
`StopAsyncIteration` arriving on a pull task means exhaustion and nothing else — it is never a
failure the consumer might have wanted to see. The exhaustion signal stays inside the iteration
protocol where it started, and the yielded type stays honestly `asyncio.Task[T]`.

### 5.5 Backpressure survives the merge

Merging is a natural place to lose backpressure, since it's tempting to let each source run and
buffer whatever arrives. `aselect` doesn't, and the reason is the one §4.1 already gave: the merge
holds at most one pull in flight per source, and is itself an async generator, so between `yield`s
it isn't running — and while it isn't running it isn't arming anything. A source can't outrun the
consumer because for as long as the consumer is away, nothing is asking it for anything.

What the re-arming line sitting **after** the `yield` rather than before it buys is not that
guarantee, but its exactness. Two sources that never await, so they'd run away instantly if
allowed, against a consumer that dawdles twenty loop passes per item, with the line in each
position:

```
re-arm AFTER the yield (what aselect does):
  consumed 1, produced 2  (ahead by 1)
  consumed 2, produced 3  (ahead by 1)
  consumed 3, produced 4  (ahead by 1)
  consumed 4, produced 5  (ahead by 1)
re-arm BEFORE the yield:
  consumed 1, produced 3  (ahead by 2)
  consumed 2, produced 4  (ahead by 2)
  consumed 3, produced 5  (ahead by 2)
  consumed 4, produced 6  (ahead by 2)
```

Neither column grows — set that against §3's ladder, where the gap passed a thousand by the fourth
item. Moving the line takes the bound from two produced-but-unconsumed items per source down to
one, which is worth having, and is a different thing from being what bounds it at all.

(`produced` is read after the consumer has finished dawdling, by which point a pull armed during
the previous `yield` has been stepped. Read it at the instant of the consume instead and the same
run yields a different ladder — one of several ways a measurement like this can be quietly
mis-stated.)

### 5.6 One last trap: what `return_exceptions=True` does not bound

Phase 1 finishes with `await asyncio.gather(*pulls, return_exceptions=True)`, and it's natural to
read that flag as drawing a line at `Exception` — collecting the ordinary failures, letting the
serious ones through. It draws no such line:

```
Exception    : gather returned [ValueError('v')]
BaseException: gather returned [Boom('b')]
KeyboardInterrupt: gather RAISED CancelledError | task.exception() -> KeyboardInterrupt
escaped asyncio.run: KeyboardInterrupt
```

`gather` stores a `BaseException` as readily as a `ValueError`. What keeps `KeyboardInterrupt` and
`SystemExit` out of that list is a different mechanism sitting one layer down — `Task`'s step
handler, which special-cases exactly those two, storing them *and* re-raising into the loop (shown
here in the pure-Python `asyncio/tasks.py`; on a default build it's the `_asyncio` accelerator that
runs, carrying the same special case):

```python
except (KeyboardInterrupt, SystemExit) as exc:
    super().set_exception(exc)
    raise
except BaseException as exc:
    super().set_exception(exc)
```

The run comes apart before the teardown gets any further, which is the outcome you want. But it is
worth knowing which line of code is producing it, because the observable ("Ctrl-C isn't swallowed
by the gather") is true while the obvious explanation for it ("`return_exceptions=True` only
catches `Exception`") is false. Anyone who later "tightens" that gather on the strength of the
explanation will be adjusting a flag that was never the thing drawing that line.

---

## 6. The API, in hindsight

The parts of the surface that look most like preferences turn out to be receipts:

| The bit that looks arbitrary | What it's actually paying for |
| --- | --- |
| Takes an **async generator**, not an iterable | The source's own I/O is still undone when the pipeline starts, so there is something left for backpressure to withhold — a list has already paid for all of it (§4.1) |
| Yields **awaitables**, not results | One item's failure is the consumer's to interpret, not the pipeline's to act on (§4.2) |
| Results come out in **completion order** | No chunk barrier, so a straggler costs one slot rather than the whole window (§2) |
| Pairs with **`aclosing`** | Async generator cleanup is explicit; the code that stops consuming is the code that knows (§3.1) |
| `aselect` **cancels before it closes** | `aclose()` cannot touch a source suspended inside its own body — and fails loudly from the cleanup path when you try (§5.1, §5.2) |
| `aselect` re-arms **after** the yield | Tightens the bound to exactly one produced-but-unconsumed item per source, rather than two (§5.5) |

None of this is exotic. It is what's left after you take the two designs everyone writes first,
run them into a consumer that stops early, and refuse to look away from what happens next.

---

**Back to** [the README](../README.md) **·** the implementations, with their reasoning written
out at length, are [`_aparallel.py`](../turbopipes/_aparallel.py) and
[`_aselect.py`](../turbopipes/_aselect.py) **·** the scripts behind every measurement above are in
[`first-principles/`](./first-principles/).

[^aparallel-teardown]: A known defect in `aparallel`'s teardown, not a property of the design it's
    part of. When a consumer leaves an `aparallel` loop early under `aclosing`, the close raises a
    `BaseExceptionGroup` wrapping the `GeneratorExit` from its own `yield`, and any exception the
    consumer left with is discarded rather than chained. The cleanup itself is correct — the source
    is closed and the in-flight tasks are cancelled — so the fix is a bug fix rather than a change
    to the shape §4.3 derives. Measured by
    [`first-principles/04_3_early_exit_today.py`](./first-principles/04_3_early_exit_today.py),
    which is expected to change when the defect is fixed.
