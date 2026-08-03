# Part II — Taking it apart

### _What the merge is made of, and which of it you need_

**[Index](../first-principles.md)** **·**
[Part I — the derivation](./part-1-the-derivation.md) **·** Part II **·**
[Part III — the API in hindsight](./part-3-the-api-in-hindsight.md)

[Part I](./part-1-the-derivation.md) ends with a merge that works. This part asks what it is made
of — because it turns out to be four separable jobs wearing one function's name, and a caller who
wants three of them should not have to take all four.

Taking something apart is easy to do badly. The failure mode is a decomposition that produces
pieces nobody would ever hold separately, sold on the strength of the diagram. So the derivation
here is run the same way [Part I](./part-1-the-derivation.md) ran: each piece is asked what it does
*alone*, the naive version of each is measured failing, and the reassembly is charged for what it
costs — which is not nothing.

Every measurement quoted is produced by a script alongside this file; the
[index](../first-principles.md#the-measurements) explains the arrangement.

---

## 6. Four jobs in one function

Here is [§5](./part-1-the-derivation.md#5-deriving-aselect--the-mirror-image-problem)'s merge again,
with the teardown left out to keep it readable:

```python
async def merge(sources):
    pulls = {asyncio.create_task(anext(gen)): key for key, gen in sources.items()}
    while pulls:
        done, _ = await asyncio.wait(pulls, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            key = pulls.pop(task)                          # identity
            if is_exhausted(task):                         # §5.4's check; no sentinel
                continue
            yield key, task                                # ...and the task, not the value
            pulls[asyncio.create_task(anext(sources[key]))] = key
```

Four jobs are tangled together in those nine lines:

1. **Interleaving.** Hold one pull per source, take whichever finishes first, re-arm it.
2. **Isolating failure.** Yield the `Task` rather than the value, so that one source's exception
   arrives at the consumer's `await` instead of ending the iteration.
3. **Attaching identity.** Carry the mapping key alongside each item.
4. **Tearing down.** Cancel every in-flight pull, then close every source, in that order and
   without losing the exception that started it.

They are tangled by accident rather than by necessity. Nothing about interleaving requires keys —
the loop uses them only to find the generator it just served, which a plain reference would do.
Nothing about wrapping a pull in a task requires a merge; a single source can be wrapped. And
identity is the most obviously bolted-on of the four: a consumer whose events already say what they
are has to invent keys (`dict(enumerate(gens))`) purely to discard them at the other end.

That is the shape of a function that grew rather than one that was designed, and it has a cost
beyond tidiness. A merge that insists on a `Mapping` cannot be used over a list. A merge that always
wraps in tasks cannot express *"if any source fails, the whole thing should stop"* without a
consumer that unwraps every item just to re-raise. Each of those is a caller paying for a job it
didn't want.

So: pull it apart. The bar for that being worth doing is higher than "the pieces exist", and it has
three rungs:

- **Each piece has to do something useful alone** — not merely be a well-named fragment of the
  original.
- **The pieces have to compose back into what you started with**, with the same behaviour, not a
  near-miss.
- **The cost has to be nameable.** Layers are not free in an event loop, and a decomposition that
  won't say what it costs is asking to be believed rather than checked.

§§7–9 take the first rung one piece at a time. [§10](#10-putting-it-back-together-and-the-bill)
takes the other two.

The fourth job — teardown — is the awkward one, and it is deliberately left until
[§11](#11-the-cleanup-extracted-asettle-and-aclosing_all). It was [Part
I](./part-1-the-derivation.md)'s hardest-won result and it lived in a single function's
`try`/`finally`. Splitting the merge in three raises the obvious worry: does that discipline now
have to be got right in three places instead of one? The answer is *two* of the three, and the
reason for both the two and the not-three turns out to be the point rather than an accident — but
it takes a section to show why.

---

## 7. `amerge`: interleaving, and nothing else

Delete jobs 2 and 3 from §6's loop and what remains is a function with a much plainer type:

```python
async def amerge(gens: Iterable[AsyncGenerator[T, None]]) -> AsyncGenerator[T, None]:
```

Generators in, items out, in completion order. No keys — the loop keeps a reference to the
generator it pulled from, which is what it actually needed. No tasks — the value is yielded.

```python
stream = turbopipes.amerge([tail('app.log'), tail('nginx.log')])

async with contextlib.aclosing(stream):
    async for line in stream:
        print(line)
```

That is the whole surface, and it is the right one for the case the keyed version served badly:
several sources that are facets of one thing, where you want the union and not the provenance.

### 7.1 What it does alone — and what it therefore can't do

Yielding values rather than tasks means `amerge` has no way to express *"this particular item
failed"*. It doesn't try. What it does instead is inherit the failure behaviour an `async for` over
a single generator already has: the exception ends the iteration.

For a merge, that has a consequence worth stating out loud — it ends *every* source's iteration,
including the ones that did nothing wrong. Three sources, one of which raises on its second pull:

```
amerge(sources)               items: good10 bad0 good20 good11
                              consumer saw: ValueError: bad blew up
                              sources closed: ['bad', 'good1', 'good2']
amerge(ataskify(g) for g ...) items: good10 bad0 good20 good11 <failed> good21 good12 good22
                              consumer saw: ran to completion (1 item failed)
                              sources closed: ['bad', 'good1', 'good2']
```

The first row is `amerge` on its own terms. Four items, then the `ValueError` arrives as the loop's
own exception and the merge comes down, taking the two healthy sources with it. Both rows close all
three sources, so this is not a leak — it is a policy, and the policy is *"a failing source is a
failing stream"*.

Sometimes that is exactly right. If the three sources are three shards of one query, a shard
failing means the answer is wrong, and carrying on with two-thirds of it is worse than stopping.
The second row is for when it isn't right, and it is not a different merge — it is the same merge
with each source wrapped in the next section's building block. That is the decomposition paying
for itself for the first time: the failure policy became a choice at the call site rather than a
property of the merge.

### 7.2 The tie-break the obvious merge doesn't have

There is a defect in §6's loop — the document's sketch, not the library's code — inherited straight
from §5, and it stays invisible right up until merging becomes its own function with its own
ordering contract to state.

`asyncio.wait` returns its completed tasks as a **`set`**. Iterating a set is iterating in hash
order, which for `Task` objects means address order — arbitrary, and different on every run. So
every source that became ready in the same event-loop pass is served in an order with no relation
to anything the caller can see or predict.

`amerge` filters instead of iterating: `[task for task in pulls if task in done]`. `pulls` is keyed
in arming order, and a source isn't re-armed until it has been served, so serving one sends it to
the back of the dictionary. That makes the tie-break **least-recently-served first**.

Eight sources that are ready on every pass, sixty-four items taken, twenty trials each:

```
iterate `done`, a set   worst service gap: 14-15 | all 20 trials identical: no
filter `pulls` by it    worst service gap: 8     | all 20 trials identical: yes
turbopipes.amerge       worst service gap: 8     | all 20 trials identical: yes
```

Two properties, and the cheap-looking one is the one that bites.

The **service gap** is how long a source can go unserved while its peers are served, counted in
items. Round-robin's is exactly the source count, by construction. Arbitrary order's worst case is
`2n - 1` — served first in one pass and last in the next — and twenty trials find it every time. A
source cannot be starved outright either way, since `wait` reports everything that completed; what
it can be is served at half the rate of a peer sitting a few slots away in the same `set`, for no
reason the caller can see.

The one that bites is **reproducibility**: same sources, same interleaving, every run. Arbitrary
order gave a different answer in essentially every trial. That is the difference between a merge you
can write a test against and one you can only write a test around.

Neither property is exotic, and neither is free: they cost one list comprehension. The reason both
are missing from §5's *sketch* is that the derivation there was chasing teardown, and a merge
presented as one step inside a keyed selector never has to answer the question *"what order do you
deliver in?"* on its own account. Giving the merge a name is what forces the answer to be written
down — and once it is written down it can be measured, which is the row above.

---

## 8. `ataskify`: whose failure is it

[§4.2](./part-1-the-derivation.md#42-why-it-yields-awaitables-instead-of-results) established the
principle for fan-out: yield the awaitable, because "one item failed" and "the pipeline failed" are
different events and only the consumer can tell them apart. §7.1 has just shown the fan-in half
needing exactly the same thing.

The question is *where to put it*, and there are two candidates. Inside the merge, which is what §5
did — the merge holds the pull as a task anyway, so yielding it costs nothing. Or in a wrapper
around each individual source, which is what the library does:

```python
async def ataskify(gen: AsyncGenerator[T, None]) -> AsyncGenerator[asyncio.Task[T], None]:
```

The wrapper wins on the first rung of §6's bar: it is usable with no merge in sight.

```python
stream = turbopipes.ataskify(read_rows())

async with contextlib.aclosing(stream):
    async for task in stream:
        try:
            row = await task
        except Exception:
            log.warning('row failed; carrying on')
```

Be honest about how much that buys over a single source. Not a great deal: the source is spent
either way, so the loop ends at the same point. What it changes is *how* the loop ends — normally,
with the failure delivered as an item you caught, rather than by an exception thrown through the
`async for` and out through whatever else your `try` block was guarding. That is a real difference
when there is other cleanup in the frame, and a small one otherwise.

The payoff arrives when there is more than one source, and the reason is the next two sections:
`ataskify` is not just a shape change. It is where the *waiting* happens, and both halves of how it
waits are load-bearing.

### 8.1 Wait on the pull; don't await it

The obvious body arms a pull and awaits it:

```python
task = asyncio.create_task(anext(gen))
await task                      # <- looks harmless
yield task
```

It isn't harmless. Awaiting re-raises whatever the source raised **inside `ataskify`'s own body**,
which makes the source's failure this generator's failure and ends the iteration — reinstating
precisely the coupling the wrapper exists to remove. `asyncio.wait([task])` returns when the task is
done and raises nothing; the outcome stays sealed in the task and is handed on intact.

A source that yields two rows and then raises, run through each:

```
await the pull    ['row-1', 'row-2']
                  ValueError: row 3 is malformed | source closed: True
wait on the pull  ['row-1', 'row-2', '<ValueError: row 3 is malformed>']
                  iteration ended normally | source closed: True
```

The `await` version never hands the third outcome to the loop at all. It closed the source
correctly — this is not a leak either — but the consumer's loop was *ended* by the failure rather
than *handed* it. One keyword, and the whole point of the layer.

### 8.2 Why the pull has to finish before it's yielded

The other half is less obvious, and looks like a pure loss when you first see it. `ataskify` yields
a task that has **already completed**. So why wrap it in a task at all? Why not yield the pull the
instant it is armed and let the consumer do the waiting — no `wait` call, the item reaches the
consumer sooner, and the task carries the waiting anyway?

Because a generator that yields an unfinished pull is parked at its `yield`, and a generator parked
at its `yield` is *instantly ready*. A merge above several of them finds every one of them ready in
the first pass, whatever their sources are actually doing. Completion order collapses into arming
order, and the waiting moves to the consumer's own `await` — one item at a time, in the order it was
handed them.

Two sources, one instant and one taking ten event-loop passes per item, merged both ways. `item@N`
reads "delivered to the consumer N event-loop passes in". The second block is the same two variants
against a different arrangement, and belongs to the backpressure paragraph below:

```
ataskify, waiting    fast0@6 fast1@12 slow0@16 fast2@18 fast3@24 slow1@30 slow2@46 slow3@62
yielding unawaited   slow0@13 fast0@13 slow1@26 fast1@26 slow2@39 fast2@39 slow3@52 fast3@52
backpressure — sources that never await, consumer dawdling 20 passes per item:
  ataskify, waiting    produced-but-unconsumed after each consume: 1 1 1 1
  yielding unawaited   produced-but-unconsumed after each consume: 1 1 1 1
```

The first row is a merge: the fast source delivers at its own rate and the slow source's items
appear among them as they become available. The second row is a queue. Everything is in lockstep at
the slow source's pace, and `fast0` — ready almost immediately — is not delivered until pass 13,
because it was handed to the consumer behind a `slow` task the consumer had to await first.

That is head-of-line blocking, reintroduced one layer above where
[§2](./part-1-the-derivation.md#2-attempt-one-asynciogather-in-chunks) removed it. The chunk barrier
came back wearing a different hat.

Worth being precise about what *doesn't* break, since it is tempting to add it to the charge sheet:
backpressure survives the eager variant intact. Each source still has at most one pull in flight and
nothing new is armed while the consumer is away, so the produced-but-unconsumed gap stays at 1 in
both arrangements — the last two rows above, measured the way
[§5.5](./part-1-the-derivation.md#55-backpressure-survives-the-merge) measures it. What is lost is
readiness ordering, and that is enough.

This is the one place where the guide contradicts the library's own prose rather than merely
supplementing it: `_ataskify.py`'s docstring says that yielding an unawaited pull would lose "both
readiness ordering and backpressure", and the second half of that is overstated — which is why the
gap is measured here instead of being asserted. The docstring is being corrected separately.

So `ataskify` has to be suspended at an `await` inside its own body between arming a pull and
handing it over. That is an unremarkable state for a generator to be in and a consequential one to
be in during teardown, because a generator in that state cannot be closed —
[§5.1](./part-1-the-derivation.md#51-aclose-will-not-touch-a-generator-thats-inside-its-own-body),
arriving one layer down from where it was first met.
[§11](#11-the-cleanup-extracted-asettle-and-aclosing_all) is the bill for this section.

---

## 9. `atag`: three lines, and one decision

```python
async def atag(key, gen):
    async with contextlib.aclosing(gen):
        async for item in gen:
            yield key, item
```

That is the entire function. It has no failure semantics, no tasks, no scheduling behaviour, and
nothing to derive about what it does. Identity, added additively — which is the point, because it
means the merge underneath never learns about keys and the keyless caller never pays for them.

What it does have is a **position in the stack**, and the two available positions are not
equivalent:

```
atag(key, ataskify(gen))  ->  tuple[str, Task[str]]     key beside the task
ataskify(atag(key, gen))  ->  Task[tuple[str, str]]     key inside the task
```

The second reads more naturally — tag the source, then wrap the tagged thing — and it is wrong.
Reading the key out of `Task[tuple[str, str]]` means awaiting the task, and awaiting a task that
raises produces the exception instead of the pair. The key is gone at exactly the moment it was
wanted.

A consumer with a per-source policy shows it. A dropped connection from `feed` is routine and gets
retried; anything from `ledger` is fatal. The policy has to be chosen from the key, so the key has
to arrive before the `await`:

```
atag(key, ataskify(gen))  ->  tuple[str, Task[str]]
  ledger=entry0
  feed=quote
  ledger=entry1
  feed ConnectionResetError -> retry
  ledger=entry2
ataskify(atag(key, gen))  ->  Task[tuple[str, str]]
  ledger=entry0
  feed=quote
  ledger=entry1
  ? ConnectionResetError -> no policy; no key
  ledger=entry2
```

Identical on every successful item, which is what makes this worth writing down: the two orders are
indistinguishable until something goes wrong, and then the wrong one has thrown away the one piece
of information the consumer needed in order to decide what to do. A design whose only difference
shows up in the failure case is a design that will be got wrong and stay wrong.

The general form of the rule: **`atag` goes outside whatever defers the value.** It is not really
about `ataskify` — it is about not burying metadata inside a container that has to be unwrapped, and
that can fail on unwrapping.

---

## 10. Putting it back together, and the bill

### 10.1 The composition is the function

```python
def aselect(gens):
    return amerge([atag(key, ataskify(gen, label=key)) for key, gen in gens.items()])
```

One expression, and `aselect`'s entire body. The order is §9's rule and §8's: `ataskify` innermost
so failures become tasks, `atag` around it so the key rides beside the task, `amerge` outermost
knowing nothing of either.

The claim being made is narrow and worth stating exactly, because the next two sections are about
the ways the composition is *not* identical to §5's function. What is identical is `aselect` and the
expression: `aselect` is that line and nothing else, so writing it out by hand gets you the same
stream rather than a lookalike with different corners. Three sources on different periods, one
failing part-way through, drained through the hand-written composition and through `aselect`
(`key!` marks a task that raised on await):

```
amerge/atag/ataskify: log0 tick0 feed0 log1 tick1 feed! log2 tick2
turbopipes.aselect  : log0 tick0 feed0 log1 tick1 feed! log2 tick2
identical           : True
```

Which also means the composition is a place to *reach past* `aselect` rather than merely an
explanation of it. Drop `atag` when the events already identify themselves. Drop `ataskify` when a
source failing genuinely should end everything — §7.1's policy, chosen deliberately. Keep both and
get the keys from somewhere other than a `Mapping`. None of that requires a new function, because
none of it was ever `aselect`'s to decide.

### 10.2 The bill, part one: scheduling

Three generator frames where there was one, and the cost is not a function call each. `amerge`
pulling on `atag` is a suspension; `atag` pulling on `ataskify` is a suspension; `ataskify` pulling
on the source is a suspension. Every one of those is a real event-loop round trip.

Sources that never await, so nothing but scheduling is being measured, and the event-loop passes
between consecutive deliveries:

```
1 source(s), 10 items delivered:
  monolith    passes between deliveries: 3 3 3 3 3 3 3 3 3 ...
  composition passes between deliveries: 6 6 6 6 6 6 6 6 6 ...
3 source(s), 30 items delivered:
  monolith    passes between deliveries: 0 0 3 0 0 3 0 0 3 ...
  composition passes between deliveries: 0 0 6 0 0 6 0 0 6 ...
```

**Six passes per item against three.** Exactly double, with no variance: it is a structural property
of the layering rather than a load-dependent one. With three sources the ratio is unchanged; the
sources are ready together, so a batch of three is delivered within one pass and the doubling lands
on the gap between batches.

What that costs in practice depends entirely on what the sources are doing. Against real I/O — a
socket, a database cursor, an HTTP round trip — three extra loop passes are lost in the noise of a
single `await`. Against sources that are already resident in memory, it is a factor of two on the
merge's own overhead. Neither is a reason to reject the decomposition; both are reasons to know the
number rather than assume it is small.

### 10.3 The bill, part two: interleaving

The passes are not only slower, they are **coarser**, and that has an observable consequence that
the arithmetic doesn't advertise.

§7.2's tie-break applies to sources completing "within the same event-loop pass". Doubling the
merge's own cycle widens the window that phrase covers — so sources the monolith could tell apart
become, to the composition, ties. Three sources on periods of one, two and three passes, drained
completely through each:

```
monolith   : a0 b0 c0 a1 b1 a2 c1 b2 a3 c2 b3 a4 c3 b4 a5 b5 c4 c5
composition: a0 b0 c0 a1 b1 c1 a2 b2 c2 a3 b3 c3 a4 b4 c4 a5 b5 c5
same items delivered      : True
per-source order unchanged: True
same interleaving         : False
```

Every item delivered by both. Each source's own items in the same order in both. The **cross-source
interleaving is different** — the composition sees all three as simultaneous and round-robins them,
while the monolith resolved their different periods.

This is worth being scrupulous about in both directions. Nothing documented changed: completion
order holds, per-source ordering holds, §7.2's tie-break holds, and the backpressure bound of
[§5.5](./part-1-the-derivation.md#55-backpressure-survives-the-merge) holds. Code that depended on a
particular cross-source interleaving was depending on something the merge never promised, and would
have been broken by a source getting slightly faster.

Equally: "you were relying on undocumented behaviour" is a true statement and a cold comfort. The
behaviour did move, it moved for everyone at once, and a test that pinned an exact output sequence
will fail. Saying so is the difference between a decomposition that was measured and one that was
asserted.

### The bill, in total

| What it buys | What it costs |
| --- | --- |
| Three pieces each usable alone, with a merge that works over a plain sequence (§7) | Six event-loop passes per delivered item against three (§10.2) |
| Failure policy chosen at the call site rather than fixed by the merge (§7.1) | Three generator frames per source: deeper tracebacks, more objects |
| Identity added additively, so a keyless caller pays nothing for keys (§9) | Cross-source interleaving moved, within unchanged guarantees (§10.3) |
| A stated, testable ordering contract for the merge itself (§7.2) | One name that has to be handed down out of band (§11.4) |

That is a trade rather than a free win, and it is the shape of trade worth taking: the costs are
constant-factor and measurable, and the benefits are structural.

---

## 11. The cleanup, extracted: `asettle` and `aclosing_all`

[§5.2](./part-1-the-derivation.md#52-what-does-reach-a-running-generator-cancellation) derived a
two-phase teardown — cancel every in-flight pull and await it, *then* close every source — and got
it right inside one function's `try`/`finally`. §6 flagged the worry that splitting the function in
three would mean getting it right in three places.

It does mean that, in two of the three layers. The two phases also came out as named, exported
functions in the process, which is worth doing on its own account:

- **`asettle(tasks)`** cancels in-flight pulls and waits for every one of them, which is what makes
  a mid-`__anext__()` generator closeable at all.
- **`aclosing_all(gens)`** closes every generator in a sequence, however the block is left.

Neither is a new idea; both are §5.2 with a name on it. What follows is why the distribution across
three layers is correct rather than merely tolerable, why the two nest one way round only, what
`aclosing_all` actually buys (less than it looks), and the one place the decomposition left a seam.

### 11.1 Every layer that arms a pull settles its own

The instinct is that one layer should own the teardown, and the reason it can't is that the pulls
are genuinely different pulls. `amerge` holds one pull per `atag` generator. `ataskify` holds one
pull per **source**. `atag` holds none at all — it is an `async for` over its input and arms
nothing, so it needs no settling, and a cancellation arriving while it waits is delivered straight
into the generator beneath it.

So there are two sets of pulls, owned by two layers, and neither can reach the other's. What makes
that work rather than merely divide the problem is that a cancellation at the top **propagates
down**: `amerge` cancels its pull on `atag`, that unwinds `atag`'s body, which unwinds `ataskify`'s,
whose own `finally` settles the source's pull on its own account.

Removing either settling is a falsification rather than an argument, so here it is, against §5's
scenario — three sources, two of them suspended inside their own body mid-pull, consumer takes one
item and leaves:

```
both settle         consumer saw: quiet
                    closed: ['chatty', 'quiet1', 'quiet2'] | frames live: none | sent to the loop: 0
ataskify doesn't    consumer saw: quiet
                    closed: ['chatty'] | frames live: ['quiet1', 'quiet2'] | sent to the loop: 2
amerge doesn't      consumer saw: RuntimeError: aclose(): asynchronous generator is already running
                    closed: ['chatty'] | frames live: ['quiet1', 'quiet2'] | sent to the loop: 0
```

Both mutants leak the same two sources: the frames are still live, and the `finally` that would have
closed the file or released the lock never ran. The mutants differ in **who finds out**, and the
difference is the wrong way round from what you'd hope.

Removing `amerge`'s settling raises §5.1's `RuntimeError` at the consumer, loudly, from the cleanup
path. Removing `ataskify`'s produces a teardown that from the consumer's side is *completely
silent* — the identical `RuntimeError` is raised inside a pull task that nobody is awaiting, so it
goes to the event loop's exception handler and the consumer's `aclosing` returns normally. Two
generators left open, two log lines somewhere, and a caller with no indication that anything went
wrong.

Which is the argument for each layer settling its own, stated the useful way round: it is not that
one layer *could* do it and this is tidier. It is that the layer holding a pull is the only one that
can see it, and a layer that doesn't settle its own fails quietly.

### 11.2 The nesting is not optional

Both phases run from cleanup paths, so their order is not the order of two statements. It is which
one is nested inside the other:

```python
async with aclosing_all(gens):        # closes, structurally OUTER
    try:
        ...
    finally:
        await asettle(pulls)          # settles, structurally INNER
```

The settling is inside, so it finishes before the closes begin — and still happens if the block is
interrupted part-way through its own cleanup. Turn it inside out and the `async with` unwinds first,
closing sources that are still mid-pull.

Both arrangements against §5's scenario, left by `break` and by `raise`:

```
break, settle inside the closes -> nothing raised
                                   closed: ['chatty', 'quiet1', 'quiet2'] | frames live: none
break, settle outside them      -> RuntimeError: aclose(): asynchronous generator is already running
                                   closed: ['chatty', 'quiet1', 'quiet2'] | frames live: none
raise, settle inside the closes -> ValueError: consumer said no
                                   closed: ['chatty', 'quiet1', 'quiet2'] | frames live: none
raise, settle outside them      -> RuntimeError: aclose(): asynchronous generator is already running
                                   closed: ['chatty', 'quiet1', 'quiet2'] | frames live: none
  consumer's ValueError anywhere in the reversed one? False
```

Note the `closed:` column: the reversed arrangement does eventually tear everything down, because
its later `asettle` still cancels the pulls that its earlier `aclose()` calls choked on. So the
symptom is not a leak, which is exactly why it is dangerous — the resources come back and a test
that checks for leaks passes. What is lost is the block's last line. The consumer's `ValueError` did
not survive: it was replaced, on the way out, by a complaint about generator state raised from the
cleanup that was supposed to be handling it.

[§5.1](./part-1-the-derivation.md#51-aclose-will-not-touch-a-generator-thats-inside-its-own-body)
called that a genuinely bad day when the merge was one function. It is the same bad day here, and
the packaging is what makes it avoidable: `aclosing_all` is a context manager rather than a
`finally` body specifically so that the nesting is a thing you can see in the indentation.

### 11.3 What `aclosing_all` buys, and what it doesn't

The appealing story about `aclosing_all` is that it keeps closing after a close that raises, where
the alternatives give up. Half of that is true, and the half that isn't is worth correcting because
it is the half people repeat.

Three sources parked at a `yield`, the first two of which raise from their own `finally`, closed
three ways:

```
for gen in gens: aclose()  closed 1 of 3: ['p1'] | escaped CleanupError: p1 cleanup blew up
nested aclosing blocks     closed 3 of 3: ['p3', 'p2', 'p1'] | escaped CleanupError: p1 cleanup blew up
aclosing_all(gens)         closed 3 of 3: ['p3', 'p2', 'p1'] | escaped CleanupError: p1 cleanup blew up
```

The construction that gives up is the **sequential loop** — its first failure abandons the rest of
it, leaving two generators unclosed. A nested stack of `aclosing` blocks does *not* give up:
`aclosing.__aexit__` ignores its exception arguments and closes unconditionally, so it keeps
unwinding exactly as `aclosing_all` does.

So the guarantee is not what distinguishes them. What does is **dynamic arity**. Nested blocks are
written lexically, one `async with` per generator, which cannot be done over a sequence whose length
is only known at runtime — which is every merge. `contextlib.AsyncExitStack` is how such a stack is
built programmatically, and `aclosing_all` is that pattern packaged, with §11.2's ordering caveat
attached to it where it will be read.

The two rows that do close everything also reproduce
[§5.3](./part-1-the-derivation.md#53-an-aside-asyncexitstack-runs-every-callback-it-doesnt-collect-their-failures):
all three sources close, and exactly one of the two cleanup failures comes out. The other is not
suppressed and not chained — it is gone. *"Every cleanup ran"* and *"you saw every cleanup failure"*
remain different guarantees, and this gives you the first.

### 11.4 The seam: naming a failure nobody can receive

Every decomposition has one place where the cut doesn't come out clean, and this is it.

A source that raises from its own `finally` while its pull is being cancelled has raised onto a
cancelled task, on a path that is already unwinding, with no consumer left to receive it. Re-raising
it would only displace the `CancelledError` or `GeneratorExit` doing the unwinding, so `asettle`
hands it to `loop.call_exception_handler()` — asyncio's route for an exception nobody can receive.
That handler is not a nicety; it is the **only** channel this class of failure has. Without it, a
source that failed to release a device does so in complete silence.

And a report that can't say *which* source failed is a great deal less useful than one that can.
Here is the seam: the layer that can **see** the failure is `ataskify`, because it is the layer
holding the source's own pull. The layer that knows the source's **name** is `atag`, which arms
nothing and sees nothing. `amerge` sits above both and knows neither. The one piece of information
and the one place that needs it are in different functions, and no reordering of the three fixes it.

Three sources, two of them mid-pull with an identical failing `finally`, merged keylessly and then
through `aselect`:

```
amerge over ataskify, no keys anywhere:
  a task raised from its own cleanup after being cancelled; there was nobody left to raise it to
    DeviceError: could not release the device
  a task raised from its own cleanup after being cancelled; there was nobody left to raise it to
    DeviceError: could not release the device
aselect, handing the key down as label:
  task 'sensor-a' raised from its own cleanup after being cancelled; there was nobody left to raise it to
    DeviceError: could not release the device
  task 'sensor-b' raised from its own cleanup after being cancelled; there was nobody left to raise it to
    DeviceError: could not release the device
```

Two reports either way — nothing is dropped by going keyless. What goes is attribution: the first
pair is indistinguishable, and an operator reading those two log lines knows that two devices are
stuck and has no idea which.

The resolution is `ataskify`'s optional `label`, diagnostics only, which `aselect` passes alongside
the tag:

```python
amerge([atag(key, ataskify(gen, label=key)) for key, gen in gens.items()])
```

The key is handed down **twice**, to two different places — `atag` carries it to the consumer,
`label` carries it to the event loop's exception handler — and that is not redundancy, because the
two destinations exist at different times. By the moment `label` is used there is no consumer left
for `atag` to have told.

It is also the strongest argument for `aselect` continuing to exist as a keyed function rather than
being retired in favour of the three. A caller composing the pieces by hand can pass `label`
themselves, and will forget, because nothing about the happy path reminds them. `aselect` is the
arrangement with the name already wired to both places.

### 11.5 The report's one gap today

One caveat on the channel §11.4 just called the only one this failure has: as the library stands
today, the report does not always happen.[^asettle-report]

`asettle` cancels the pulls, waits for them with `gather(..., return_exceptions=True)`, and *then*
reports what each cancelled pull came back holding. The reporting is after the wait, and the wait
does not always return. A further `cancel()` arriving while that gather is open cancels the
**gather**, which then raises `CancelledError` once its children finish
([gh-32684](https://github.com/python/cpython/issues/32684)) — and `return_exceptions=True` does not
prevent it, because that flag governs what the *children* raise, not what is done to the gather
itself. Which is
[§5.6](./part-1-the-derivation.md#56-one-last-trap-what-return_exceptionstrue-does-not-bound)'s
lesson arriving in a second costume: the flag is not the thing drawing the line you think it is.
Leaving by that route skips the reporting entirely, and the failure is dropped.

The window is not an instant — it is the whole duration of the teardown's gather, so it widens with
however long the sources take to clean up. Sweeping the gap between two cancellations in event-loop
passes, against sources whose own cleanup awaits once and three times (`1` = reported, `0` =
dropped):

```
cleanup awaits 1x      1  0  0  0  0  1  1  1  1  1  1  1
cleanup awaits 3x      1  0  0  0  0  0  0  1  1  1  1  1
                       0  1  2  3  4  5  6  7  8  9 10 11
                       ^ event-loop passes between the two cancellations
```

The damage is confined to a lost diagnostic. Every source is still closed, its own cleanup still
runs, no task is left pending, and no cancellation is swallowed — §11.1's and §11.2's results are
unaffected. It is worth knowing anyway, because it is the difference between "this failure is always
reported" and "this failure is reported unless the teardown is itself interrupted", and only one of
those is a thing to build an alerting story on.

---

## Where this leaves off

Six sections in, the merge is four functions and a rule about how to stack them, each piece is
usable alone, the reassembly is checked rather than asserted, and the bill has been added up.

What is left is to go back over the whole surface — `aparallel` included — and ask which layer is
actually responsible for each of the properties the library advertises, several of which
[Part I](./part-1-the-derivation.md) credited to `aselect` wholesale because at the time there was
nothing else to credit. That is
**[Part III](./part-3-the-api-in-hindsight.md)**.

---

**Back to** [the index](../first-principles.md) **·** back to
[Part I](./part-1-the-derivation.md) **·** on to
[Part III — the API in hindsight](./part-3-the-api-in-hindsight.md) **·** the scripts behind every
measurement above are in [this directory](./README.md).

[^asettle-report]: A known defect in `asettle`'s reporting, not a property of the design §11.4
    derives. When a second cancellation lands inside the teardown's own `gather`, the gather raises
    rather than returning and the reporting that follows it never runs, so a source's cleanup
    failure is dropped. Nothing leaks and no cancellation is swallowed; what is lost is one log
    line. Measured by [`11_5_report_gap_today.py`](./11_5_report_gap_today.py), which is expected to
    change when the defect is fixed.
