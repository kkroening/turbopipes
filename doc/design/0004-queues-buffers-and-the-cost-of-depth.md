# 0004 — Buffering, `asyncio.Queue`, and what depth actually costs

**Status:** exploratory, empirically grounded. Frame:
[0001](0001-axes-between-channels-and-generators.md).

**Environment:** CPython 3.14.6. `pyproject` declares `^3.11`; 3.11 was not exercised.

## What this had to settle

0001 claims the channel/generator gap is *quantitative* on the depth axis — that a buffering
wrapper turns a 0-deep generator into an n-deep one, making "channels push, generators pull" a
difference of degree. It also claims the price is fixed: every move to depth > 0 introduces an
independently scheduled producer, which is exactly the thing that needs supervision.

Both claims survive. The second one is more expensive than it sounds.

## The depth claim holds, and is measurable

Three items, 50 ms to produce, 50 ms to consume. Serialized floor 300 ms; fully-overlapped floor
200 ms:

```
bare async generator (0-deep baseline)  TOTAL 306 ms
abuffer(capacity=1)                     TOTAL 205 ms
abuffer(capacity=2)                     TOTAL 204 ms
abuffer(capacity=4)                     TOTAL 204 ms
```

Producing and consuming genuinely overlap — the bare run produces at 51/153/255 ms, the buffered one
at 51/102/153 ms. **Capacity 1 already saturates a balanced pipeline**, so this benchmark cannot
show capacity mattering; a bursty one can (8 items, 20 ms each, consumer stalls once for 300 ms):

```
bare            469 ms      abuffer(cap=2)  406 ms
abuffer(cap=1)  427 ms      abuffer(cap=8)  343 ms
```

Two things worth writing down before anyone designs an API around this:

**Depth is `capacity + 1`, not `capacity`.** The pump holds one further item in hand while parked in
`put()`. Verified across the board — cap 1/2/4/8 give max-in-flight 2/3/5/9.

**`capacity=0` is a trap.** By analogy with Go's `make(chan T)` it reads as "rendezvous"; but
`asyncio.Queue(maxsize=0)` is *unbounded*, and the probe duly ran 19 of 20 items ahead. Any public
`abuffer` must reject or remap 0 rather than pass it through.

## The finding that matters: the obvious implementation deadlocks

The natural `abuffer` — pump task, envelope queue, forward exceptions into the queue — has a
**timing-dependent hang** in its teardown path. It fires when the source raises from its own
`finally` in response to cancellation, at which point the `RuntimeError` *replaces* the
`CancelledError`. Same source, same failure, outcome decided purely by where the pump was parked:

```
buffer NOT full, pump parked in anext()   cancelled=False done=True exc=None
                                          queue holds 1 item, 1 exc envelope
buffer FULL,     pump parked in put()     pump ended RuntimeError    <- correct
buffer FULL,     pump parked in anext()   **WEDGED**: still pending 1 s after cancel()
                                          parked at `await queue.put((EXC, exc))`
                                          a SECOND cancel() broke the wedge
```

`asettle` issues exactly one `cancel()` per task, so this is a genuine hang, not a hypothetical. At
the `abuffer` level, with an ordinary consumer under `contextlib.aclosing`:

```
aclose() with a FULL buffer + failing source cleanup  ->  **aclose() HUNG**, still pending 1 s later
aclose() with room in the buffer                     ->  returned; failure vanished entirely
                                                         (no warnings, no loop reports)
```

Both outcomes defeat the `asettle` / `_report_cleanup_failure` machinery: the pump's
`except Exception` catches a failure that *replaced a cancellation*, so the task reports success
after being cancelled, and the cleanup failure sits unread in a queue that is about to be discarded.

This is the shape of bug that passes tests and hangs in production — it needs a full buffer, a
failing cleanup, and the pump parked on the wrong side of an await.

### The fix

One shared flag — "nobody is draining any more" — set *before* `asettle` cancels, so the pump never
blocks on `put()` once the consumer is gone, and re-raises rather than swallowing during teardown:

```python
async def _pump(gen, queue, state):
    async with contextlib.aclosing(gen):
        try:
            async for item in gen:
                await queue.put((ITEM, item))
        except Exception as exc:
            if state.closing:
                raise                       # hand it to asettle; never block on put
            await queue.put((EXC, exc))
        else:
            if not state.closing:
                await queue.put((EOF, None))


async def abuffer(gen, capacity=1, *, label=None):
    queue = asyncio.Queue(maxsize=capacity)
    state = _PumpState()
    task = asyncio.create_task(_pump(gen, queue, state))
    try:
        while True:
            kind, payload = await queue.get()
            if kind == ITEM:
                yield payload
            elif kind == EOF:
                break
            else:
                raise payload               # original exception, traceback intact
    finally:
        state.closing = True
        await asettle([task], label=label)
```

Both scenarios then land on the documented contract:

```
aclose() with a FULL buffer + failing source cleanup
    aclose() returned; no error
    loop-exception-handler: task 'src' raised from its own cleanup after being
      cancelled; there was nobody left to raise it to | RuntimeError: source cleanup itself failed
```

### With that fix, the ownership model survives intact

Eleven teardown scenarios at capacities 1 and 4, each in its own loop with an exception handler and
warning capture — all clean:

| | |
| --- | --- |
| break early under `aclosing` | source finalized, 0 live tasks |
| walk away with no `aclose` | finalized after GC, 0 live tasks |
| upstream raises mid-stream | reaches consumer **in stream order**, traceback intact through all three frames |
| consumer cancelled while blocked in `anext()` | `CancelledError`, source finalized, 0 live tasks |
| source `finally` raises / awaits | reported with the right label / allowed to complete |
| `ataskify(abuffer(...))`, `amerge([abuffer(...), ...])` | clean, 0 live tasks |

Cancellation propagates through the pump into the source's frame exactly as turbopipes relies on,
because the pump owns `gen` via `aclosing` and the wrapper owns the pump via `asettle`. Ownership
still begins at first advance — a never-advanced wrapper leaves `gen` untouched and the caller's to
close, matching `ataskify`'s documented rule.

`aclose()` mid-pull raises `RuntimeError: aclose(): asynchronous generator is already running`,
which is **identical to `ataskify`** — a known discipline, not a new problem.

## The two costs that are inherent, not bugs

These cannot be engineered away, because they *are* depth. They belong in the docstring, not the
issue tracker.

**Buffered items are discarded silently on early exit.** At capacity 4: `produced=[0,1,2,3,4]`,
`delivered=[0]`, and `[1,2,3,4]` dropped with no diagnostic. With a bare generator those items would
never have been produced at all. Where producing has side effects — an HTTP request, an advanced DB
cursor, a consumed Kafka offset — **buffering has already committed them.** That is the sharpest
practical difference between depth 0 and depth n, and it is invisible at the call site.

**A failure discovered while running ahead can vanish.** Source fails at item 2, consumer walks away
after item 0:

```
produced by source   : [0, 1]
delivered to consumer: [0]
note: consumer exited cleanly - saw NO error      (no warnings, no loop reports)
```

The failure genuinely happened, was captured, and was thrown away. A bare generator would never have
reached it. Worth routing a discarded `EXC` envelope to the loop exception handler on teardown —
the same treatment `asettle` gives a cleanup failure with nobody to raise it to.

## `asyncio.Queue` interop: four gaps, all demonstrated

**No EOF.** Without a terminator the `async for` never ends. Sentinels work for one consumer and
break for N — `3 consumers, 1 sentinel: 1/3 finished; 2 consumers HUNG` — so the producer must know
the consumer count at shutdown time. Worse, a sentinel widens the element type and can collide with
a payload: five payloads, one of which *is* the sentinel, gives `3 payload(s) silently truncated`
with three orphaned items left in the queue.

**No exception propagation.** Producer fails at item 3 → `consumer got [0,1,2], done=False`, hung
forever, because no sentinel was ever put. Through a plain generator the same failure goes `got
[0,1,2] then ValueError -- straight to the consumer`.

**`join()`/`task_done()` is a second protocol on the same object.** Without `task_done()`, `join()`
hangs; an early-exiting consumer leaves the accounting inconsistent (`consumed 2 of 5, unfinished=3`);
over-calling raises `ValueError: task_done() called too many times`.

**No backpressure when unbounded.** 300 ms with no consumer: `maxsize=0 → qsize=200001 (+8.0 MB)`
versus `maxsize=4 → qsize=4`.

**And no fairness.** `Queue.get()` returns without suspending whenever the queue is non-empty, so a
consumer whose body never awaits drains everything: `partitions=[[0,1,2,3,4,5], [], []]`. `amerge`
documents deliberate least-recently-served ordering; a Queue guarantees nothing.

### `Queue.shutdown()` (3.13+) closes one gap and sharpens another

It is the right terminator where available: one call ends all N consumers, no sentinel, no type
widening. Two caveats. `shutdown(immediate=True)` drops the buffer silently (`5 items buffered ->
got=[]`); `immediate=False` drains first. And it still **cannot carry a failure** — under
`shutdown()` a consumer whose producer died completes *successfully* with a truncated stream
(`consumer saw an error? None`). Truncation that looks exactly like success is the more dangerous of
the two failure shapes, not the safer one. Also: `^3.11` means it is not universally available.

## Should a buffered generator be `asyncio.Queue`-compatible? — No.

It is achievable. `AsyncGenQueue(asyncio.Queue)` with `__aiter__` is a genuine drop-in:
`isinstance(q, asyncio.Queue) = True`, the classic producer/worker/`join()` round-trip works
unmodified, and the same object iterates with `async for`.

It should still not ship, and the reason is that it is *easy*:

- **`task_done()` collides.** The iterator must call it or `join()` hangs; a Queue-idiom consumer
  calls it too. Result: `ValueError: task_done() called too many times`, escaping into
  `asyncio.run()` shutdown. Two protocols claim one method and cannot coexist.
- **No EOF in the interface.** A producer *typed* to `asyncio.Queue` has nothing to call that means
  "done" — `close() on the Queue interface? False` — so the consumer hangs.
- **Ownership is gone.** Closing the iterator side does not reach upstream: `producer still running:
  True; producer finalized: False`. A queue structurally cannot have this property, because it does
  not know who fills it.
- **Partition, not broadcast**, with no `RuntimeError` to warn you — unlike two consumers on a
  generator.

The object would type-check as an `asyncio.Queue` while silently lacking the three things turbopipes
exists to provide: EOF inside the iteration protocol, exception propagation, and teardown through
one handle. `isinstance` returning `True` is an assertion to every reader and every type-checker
that Queue semantics apply, and the `task_done()` collision proves they do not. The failure lands as
a `ValueError` from inside a third-party worker loop that was written correctly against the
documented contract.

**The defensible shape is the opposite:** keep the async generator as the type, and offer explicit
adapters at the boundary — `queue_to_gen` / `gen_to_queue` — that force the caller to decide, at the
call site, what EOF means, who observes producer failure, and whether the queue is bounded. Build on
`Queue.shutdown()` rather than sentinels where 3.13+ is available. It still cannot carry a failure,
and that residue is precisely where an adapter must be explicit rather than clever.

## Open items

1. Route a discarded `EXC` envelope to the loop exception handler, so a run-ahead failure is not
   silently lost.
2. Reject or remap `capacity=0`.
3. Document depth = `capacity + 1`, and document the side-effect consequence of run-ahead at least
   as loudly as the speedup.
4. Decide the `^3.11` floor's effect on leaning on `Queue.shutdown()`.
