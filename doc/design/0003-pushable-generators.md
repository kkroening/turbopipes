# 0003 — Producer-side handles

**Status:** exploratory, empirically grounded. Frame:
[0001](0001-axes-between-channels-and-generators.md).

**Environment:** CPython 3.14.6. `pyproject` declares `^3.11`; 3.11/3.12 unverified.

## The question

0001's Axis 3 says we have no *producible* handle. There is no `await gen.push(x)` — to produce you
must be inside the generator body, where you produce because you were asked to, not on your own
volition. Can we get a handle you push into, whose consumer side stays an ordinary async generator?

Yes. But the obvious API shape is the one that cannot work, and the reason generalises.

## `asend` is not the mechanism, and the reason is sharper than "single consumer"

**`asend` *is* `anext` with an argument. The push and the pull are one operation.**

```
('yielded', 0, [])
asend("A") -> ('yielded', 1, ['A'])
asend("B") -> ('yielded', 2, ['A', 'B'])
```

Every `asend` *returned a value*. So a third party "pushing" via `asend` is not producing into the
stream — it is **stealing items from the consumer**:

```
consumer received: [('yielded', 0, []), ('yielded', 2, ['A', None]), ...]
producer received: [('yielded', 1, ['A']), ('yielded', 3, ['A', None, 'B'])]
```

Corrections to the usual folklore, all verified: there is **no identity restriction** — any task
holding the generator may `asend`; the real restrictions are re-entrancy (concurrent callers get
`RuntimeError: anext(): asynchronous generator is already running`) and the fact that the sender
receives an item. `athrow` also advances the generator, so it is no better as an out-of-band
channel. A pure sink (`while True: x = yield`) *is* driveable by `asend`, but its consumer side
yields `None`s — it is a sink, not a source, and nothing downstream can `async for` it.

## `pushable()` works, and the raw shape has three hangs

Two shapes were built:

```python
handle, gen = pushable(capacity=1)      # raw: nothing owns the producer
gen = pushed(producer_fn, capacity=1)   # owned: the generator owns the producer task
```

The consumer side is a genuine, unwrapped `AsyncGenerator` in both. That part is not ugly at all.

**Depth is exactly `capacity + 1`** — the same off-by-one as `abuffer` in
[0004](0004-queues-buffers-and-the-cost-of-depth.md), and it corrects an assumption we were carrying:

```
capacity=0: 0 accepted without blocking, + 1 parked in the blocking push = depth 1
capacity=1: 1 accepted ...                                               = depth 2
capacity=5: 5 accepted ...                                               = depth 6
```

So **`capacity=0` is the unbuffered-channel rendezvous, not `capacity=1`.** "A one-slot buffer
mirrors an unbuffered channel" is off by one; at capacity 1 the first `push` returns immediately.

The depth buys the expected overlap — 0.41 s serialized bare versus 0.25 s pushed — and backpressure
blocks and resumes in the *same event-loop pass* as the drain (`push blocked for 0.201s`, returning
at `t=0.201` on the tick the consumer took an item). `close()` is drain-first; `fail(exc)` surfaces
at the consumer's `anext()` **with the producer's own frame still in the traceback**.

### The three hangs, all in the raw form

| | Scenario | Result |
| --- | --- | --- |
| 1 | producer task **raises** without `close()` | consumer waits **forever** |
| 2 | producer task **cancelled** | consumer waits **forever** |
| 3 | generator **never advanced**, then `aclose()`d | parked `push()` blocked **forever** |

Hangs 1 and 2 are structural, and no care inside the adapter can fix them: **the raw handle has no
lifetime link to the producer, so nothing can synthesize EOF for a producer that died.** Hang 3 is
turbopipes' own "ownership begins when iteration does" rule biting from the other side — an
unstarted generator's `aclose()` never runs the body, so the shutdown path never fires.

Notably there is **no "Task was destroyed but it is pending" warning in any scenario**. The leak
shows up only as a permanently parked task, visible if you check `all_tasks()` before loop shutdown.
A hang here is silent.

### And one lost item that is not our fault

```
after handoff: pull.done()=False  pusher.done()=True
pull cancelled=True; push outcome=None (success)
next pull -> StopAsyncIteration: the generator is FINISHED
item 42 still stranded in the buffer: [42]
=> push() reported SUCCESS for an item the consumer never saw.
```

The root cause is not the adapter: **cancelling an in-flight `__anext__()` ends the generator
outright**, and that is true of any async generator — a plain one gives `StopAsyncIteration` after a
cancelled pull too. Any at-most-once / at-least-once story has to be built on top of that fact
rather than assuming it away.

### `pushed()` fixes all three, structurally

Inverting to a callback, so the generator owns the producer task:

```
producer raises           -> consumer got [0], then RuntimeError: producer blew up
producer returns          -> consumer got [0, 1, 2], clean EOF
consumer breaks early     -> producer saw CancelledError; live tasks now: 0
producer cancelled ext.   -> consumer got [0], then ProducerCancelled
never advanced            -> tasks created: 0 (nothing to strand)
```

**This should be the blessed API.** `pushable()` is worth keeping only as the unsafe primitive
underneath it, documented as such — the tuple-returning shape is precisely the "hand out a handle
with no lifetime link" move that 0001 predicted would cost supervision.

## n:1 fan-in works, with a measurable bias and a coordination gap

Three producers × five items → 15 received, exactly once each, per-producer order preserved.

**Fairness:** no starvation, but a 2:1 bias toward whoever is first in the putter queue —
`counts: {'a': 7, 'b': 3, 'c': 3, 'd': 3}`. Cause (inferred from the trace): the direct-handoff fast
path lets the first-resolved producer find the consumer's getter already parked and hand off
immediately, gaining one extra item per round. Worth contrasting with `amerge`, which documents
deliberate least-recently-served ordering.

**`close()` is channel-wide** — one producer closing kills its peers mid-stream and loses their
remaining items. There is no built-in coordination. A `TaskGroup` inside `pushed()` fixes it: 12/12
delivered with clean EOF and 0 live tasks, and a failing leg arrives at the consumer as an
`ExceptionGroup`.

## Select on writability: the asymmetry that decides everything

It composes trivially at the `asyncio.wait` level — an armed pull and a `wait_writable()` future sit
in one wait set with no adaptation (`types in the wait set: Task, Future`).

But the two sides are not the same kind of thing, and this is the finding worth carrying back into
[0001](0001-axes-between-channels-and-generators.md):

```
(a) discard an arm that ALREADY completed:  next item is 1   <- 0 IS LOST
(b) cancel an arm that is still PENDING:    StopAsyncIteration - THE SOURCE IS DESTROYED
(c) discard 6 writability futures:          a fresh wait_writable() still fires - nothing lost
```

> **Arming a read is a reservation. Arming a write is a predicate.**

A read-arm is destructive: it commits the source to producing an item (0002's "peek costs exactly
one item"), and it must therefore be *owned* across select passes. A write-arm binds nothing and can
be discarded freely. That difference is why unix `select`'s symmetry between read-sets and
write-sets does not carry over: the two halves have different lifetimes.

**The working shape** — arm write-readiness only while holding an item, exactly as one only puts an
fd in `select`'s write set when there is something to send:

```python
while True:
    if pend is None:
        read = read or asyncio.ensure_future(anext(src))   # armed pull: OWNED across passes
        waits = {read, control}
    else:
        waits = {out.wait_writable(), control}             # free to discard
    done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    ...
    if out.try_push(pend):   # re-check: wait_writable() is a hint, not a reservation
```

It aborts cleanly from a control future even while blocked on the write side.

**Writability cannot be a stream inside `aselect`/`amerge`.** Both encodings fail:

- level-triggered `awritable()` → busy spin, `in 0.10s: {'read': 1, 'write': 1029}`
- edge-triggered → no spin, but once the edges stop the merge **stalls forever**

And `wait_writable()` is a hint rather than a reservation — two waiters both fire, then
`writer 1 try_push -> True`, `writer 2 try_push -> False`. The same check-then-act race as
`select(2)`; the `try_push` re-check is mandatory, not defensive style.

## Integration with turbopipes is clean

`pushed()` behaves as a proper source: `amerge`/`aselect` with early break tear the producers down
(`live tasks after teardown: 0`); in-flight pull cancellation — the `asettle` route — unwinds the
producer and finishes the generator; `aclose()` mid-pull gives the documented `RuntimeError`; a
failing pushed source brings the merge down per `amerge`'s rule. **Zero leaked tasks in every case.**

## Assessment

The consumer side genuinely stays an ordinary async generator, so a producer-side handle does not
fracture the model. What fractures it is the **tuple-returning** shape: handing out a producer handle
with no lifetime link to the consumer side produces all three hangs, and the adapter cannot fix what
it cannot observe. `pushed(producer_fn)` is the only shape where the invariants hold structurally.

Two limits Python imposes regardless of API design:

1. **Cancelling an in-flight `__anext__()` destroys the generator**, so a lost item is always
   reachable via consumer-side cancellation.
2. **Write-readiness cannot be a stream in a merge** — it must be an on-demand awaitable, because a
   predicate that is continuously true is not an event.

## Open items

1. Blessed API is `pushed(producer_fn)`; `pushable()` unsafe-primitive only, so documented.
2. `TaskGroup` inside `pushed()` for the multi-producer case, so one leg's `close()` cannot strand
   its peers.
3. Document depth = `capacity + 1` and that **`capacity=0`** is the rendezvous.
4. Decide whether the fan-in fairness bias is worth correcting to match `amerge`'s
   least-recently-served guarantee, or documenting as unspecified.
