# 0002 — Is a one-shot `select` already available?

**Status:** exploratory, empirically grounded. Frame: [0001](0001-axes-between-channels-and-generators.md).

**Environment for every measurement below:** CPython 3.14.6. `pyproject` declares `^3.11`, which was
not exercised — treat the 3.11 behaviour as unverified.

## The question

`aselect` is Go's `select` *in a `for` loop*. Go's `select` is the single pass. Is there a one-shot
primitive — "give me a read from whichever source is ready first" — and do we already have it?

Provisional answer going in: `anext(aselect(...))` probably is it, but only if you hold the same
generator object across passes, since the losing armed pulls live in that generator's frame.

That turned out to be right about the conclusion and **wrong about the reason**, in a way that
matters for anything built on top.

## Finding 1: yes, if you hold it — and it is not a near-equivalent

Hand-driving `anext()` on one held `aselect` is byte-identical to `async for` over it: same items,
same order, same timestamps.

```
[('fast','fast0'), ('fast','fast1'), ('slow','slow0'), ('fast','fast2'), ('fast','fast3'), ('slow','slow1')]
```

Fairness survives too. Four simultaneously-ready sources drained by hand-driven `anext()` give
`wxyzwxyzwxyz` — the same least-recently-served round-robin as the `async for` path.

**So the one-shot select already exists.** It is spelled `await anext(sel)` on a held `sel`, and a
`for { select {} }` written by hand around it behaves exactly like the loop we ship.

## Finding 2: the failure mode of *not* holding it is worse than "loses in-flight items"

This is the correction. A fresh `aselect(...)` per pass does not merely re-arm — **it destroys the
sources**, because `amerge` owns what it is given and closes all of it on the way out:

```
   0ms  --- pass 0: constructing a fresh aselect over the same 2 sources
  51ms  pass 0: CONSUMER got fast=fast0; now closing this aselect
  51ms    [slow] CANCELLED mid-compute of slow0
  51ms    [slow] finally (closing)
  51ms    [fast] GeneratorExit (aclose)
  52ms  pass 1: aselect ended immediately (StopAsyncIteration)
  RESULT: [('fast', 'fast0')]
```

One item, then silence. And leaking rather than closing each pass is not an escape — the previous
select still holds an armed pull, so the next one collides:

```
  53ms  pass 1: awaiting the winning task RAISED RuntimeError('anext(): asynchronous generator is already running')
  54ms  !! loop.call_exception_handler: 'Task exception was never retrieved'
        exc=RuntimeError('aclose(): asynchronous generator is already running')
```

Holding the `atag(ataskify(...))` wrappers and rebuilding only the `amerge` fails identically — the
merge owns whatever it is handed, one layer down.

**Nothing is ever duplicated.** Three fresh selects over one source yield `['s0']`. The failure mode
is loss and `RuntimeError`, never double delivery — which is the right way round, but it is silent
loss, so it is not self-announcing.

The mechanism is therefore **ownership, not lookahead**. That reframes 0001's cost table entry: a
one-shot select costs nothing *provided the arms outlive the call*, and the thing that makes them
outlive it is an owner that persists — which is exactly the property we get for free at depth 0 and
must arrange deliberately for anything else.

## Finding 3: the armed pull really is the peek, and peeking costs exactly one item

| Question | Answer |
| --- | --- |
| Does a completed pull hold its value? | Yes — indefinitely, and re-readably. Held 150 ms: `done=True`, then `-> 'src0'`, then again `-> 'src0'`. |
| Does it survive `aclose()` of the source? | **Yes** — the source is parked at its yield, so closing it does not disturb the held value. |
| `aclose()` while the pull is in flight? | `RuntimeError('aclose(): asynchronous generator is already running')`. Settle first, then close. |
| Drop the future un-retrieved? | **Value: silently lost, zero diagnostic.** |
| Is bare `anext(gen)` the peek? | **No** — unscheduled it never advances anything, and dropping it warns `coroutine method 'asend' … was never awaited`. `ensure_future` is the peek. |

The part the model has to price: **arming runs the producer.** Arm two sources, take one, and the
other has still produced — side effects `['x0', 'y0']` with nobody asking for `y0`. Peeking commits
a source to producing exactly one item. That is not a wart; it *is* the 0-deep → 1-deep purchase
from 0001, and there is no cheaper peek available at the generator interface.

**And through `aselect` you cannot decline.** `ensure_future(anext(sel))` peeks safely at the merge
level, but the merge has already committed, and a peer that produced meanwhile is discarded on
close, silently:

```
  51ms    [aaa] yielding aaa0
  51ms    [bbb] yielding bbb0
  51ms  CONSUMER got aaa=aaa0; closing now
  51ms    [bbb] GeneratorExit (aclose)
  RESULT: consumer saw exactly [('aaa', 'aaa0')]
```

`bbb0` existed, reached nobody, warned nobody. Go's `select` with no matching case has no expression
here.

## Finding 4: teardown is hermetic — with three sharp edges

The good news is unambiguous. `aclose()` with two losers mid-`await`:

```
  31ms  TASK CENSUS before close: 4 other task(s) alive
  31ms    [slowA] CANCELLED mid-compute / finally (closing)
  31ms    [slowB] CANCELLED mid-compute / finally (closing)
  31ms    [winner] GeneratorExit (aclose) / finally (closing)
  31ms  TASK CENSUS after close: 0 other task(s) alive
  33ms  warnings: NONE          loop exception-handler reports: 0
```

No pending-task destruction, no un-awaited coroutines, nothing leaked. The same holds when the
consumer task is cancelled instead of closed, when a source needs an `await` in its own cleanup, and
when the select is leaked entirely (interpreter shutdown cancels a leaked mid-pull select cleanly,
exit 0). **A one-shot select can be made hermetic.** Named failures work too: a losing source that
raises from its own `finally` is reported with its label.

The three edges:

1. **Swallowed cancellation loses an item with no diagnostic.** A source that catches
   `CancelledError` and yields anyway leaves a pull holding a real item — `cancelled()=False`,
   `exception()=None`, `result()='stubborn0'` — an item that reached nobody. `asettle` sees nothing
   wrong, so nothing is reported.
2. **Winner/loser cleanup failures use different channels.** A *losing* source's cleanup failure is
   absorbed and reported to the loop handler; a *winning* (parked-at-yield) source's cleanup failure
   **escapes out of `aclose()`**. Same teardown, two exits, depending only on which source it was.
3. **A non-last cleanup failure vanishes.** Two parked sources both raising: one escapes chained to
   `GeneratorExit`, the other is neither chained nor reported. `aclosing_all`'s docstring owns this,
   but invisible is worse than lossy.

## Finding 5: task-vs-item — three shapes, and the key is the discriminator

| | **A** `next_task() -> (key, done Task)` | **B** `next_item() -> (key, item)` | **C** `next_read() -> pending Task[(key, item)]` |
| --- | --- | --- | --- |
| Key available before value | yes | n/a | **structurally never** |
| Source failure | attributed; loop continues | arrives keyless | arrives keyless |
| Ownership of the read | caller's | primitive's | primitive's |

Error routing, measured: shape A gives `bad FAILED with RuntimeError('bad exploded') <- attributed,
loop continues`, then carries on to `ok -> 'ok0'`. Shape B gives `FAILED with RuntimeError('bad2
exploded') <- which source was that?` and the key is unrecoverable.

Shape C **cannot carry a key at all** — while pending there is nothing to inspect, so there is
nothing to hang a per-source timeout or retry on, and when it resolves by raising the key goes with
it. "A task representing the next read from whichever source is ready" is therefore *strictly
weaker* than what `aselect` already yields. Shape A is the right currency; this is
`atag`-outside-`ataskify` restated as a one-shot API.

## Finding 6: the real dividing line is where the arms live, not task-vs-item

Cancel a pending read whose arms live **in a generator frame** (a held `aselect`) and the
`CancelledError` unwinds the merge — the whole select dies:

```
  51ms  cancelling the pending read (a timeout, say)
  51ms    [fast] CANCELLED mid-compute ... [slow] CANCELLED mid-compute
  51ms  after cancel: sel.ag_frame is None -> True
  51ms    StopAsyncIteration - THE WHOLE SELECT IS DEAD
```

Cancel the same read whose arms live **in a state object** and `asyncio.wait` does not touch what it
waits on, so the arms survive:

```
  51ms  armed keys: ['fast', 'slow']; cancelling the pending read
  51ms  after cancel: still armed -> ['fast', 'slow']  (sources untouched)
 301ms    got fast='fast0'  <- NOTHING WAS LOST
```

The practical consequence is severe and non-obvious: **`asyncio.timeout()` around `anext(sel)`
destroys the select.** The idiom every user will reach for first —

```
attempt 0: timed out; trying again...
attempt 1: StopAsyncIteration -- select is dead
```

A state object is immune, and it is the only shape that can express `peek()` synchronously and
*decline a pass* — `wait_ready() -> ['a']`, decline, and 100 ms later `peek() -> ['a','b']` with
both held and nothing lost. That is Go's `select` with a `default:`, which the generator-based
select cannot say.

## Footgun found in shipped code: `is_exhausted()` disarms asyncio's safety net

This one is not about a proposed API. It is about what we already ship, and it is the most
consequential finding here.

`is_exhausted()` calls `task.exception()`, and `task.exception()` clears `Task._log_traceback` —
the flag that produces `Task exception was never retrieved`. Both `ataskify` and `amerge` call
`is_exhausted` on every pull, so **every task `aselect` hands out arrives pre-silenced**:

```
control: drop a failed task                    -> 'Task exception was never retrieved'
after is_exhausted(task): _log_traceback=False -> loop reports: NONE
```

End to end through real `aselect`, a consumer that ignores a failing task gets **nothing at all** —
no exception, no warning, no loop report. Contrast `amerge` alone, where the same failure is
impossible to ignore.

The reason this matters more than a normal rough edge: `ataskify`'s entire value proposition is
*the consumer decides what to do with a failure*. The safety net for a consumer that decides
nothing is switched off by the very mechanism that offers the choice. A user who forgets to check
gets silence, and silence is indistinguishable from success.

Worth its own fix, separately from anything in this document.

## Where this points

The one-shot select exists today and is free, exactly as 0001's cost table predicted — but only in
the held-generator form, and that form carries two traps that will find users: reconstructing it
destroys the sources, and wrapping it in `asyncio.timeout()` destroys the select.

The results argue for a **`Select` state object** as the eventual shape: arms in a dict rather than
in a frame buys cancel-safety, a real synchronous `peek()`, the ability to decline a pass, and a
teardown that is a plain settle-then-close (verified clean). Its cost is honest and worth stating —
it is a second ownership model standing alongside "the generator is the handle", and it does not
compose with `async for`.

Shape **A**, `(key, done task)`, is the right currency for it. Shape C is strictly weaker; shape B
throws away the key that makes failures attributable.

Recommended sequencing, deferred to Karl:

1. Fix the `is_exhausted` silencing — independent of every design question here.
2. Document the two traps against the primitive we already have.
3. Only then decide whether `Select` earns its second ownership model.
