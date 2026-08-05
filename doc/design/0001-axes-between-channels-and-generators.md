# 0001 — The axes between channels and generators

**Status:** exploratory. Frames the design questions in [0002](0002-single-item-select.md),
[0003](0003-pushable-generators.md) and [0004](0004-queues-buffers-and-the-cost-of-depth.md);
decides none of them.

## Why this document exists

The first-principles guide used to explain the Go analogy like this:

> **What it doesn't get: push.** Channels push, generators pull.

That is wrong, and finding out *why* it is wrong turned out to be more useful than the claim it
replaced. This document records the corrected model, because every extension we are contemplating —
a one-shot `select`, a pushable producer handle, buffering, `asyncio.Queue` interop, actors — is a
move along one of the axes it identifies, and each axis has a characteristic price.

## What is not the difference

**Direction of data.** A value crosses from producer to consumer at the hand-off either way. `yield
v` and `ch <- v` both hand a value over. Nothing is "pushed" in one and "pulled" in the other.

**Threads.** Goroutines do not own threads; they are M:N-scheduled onto a pool, and Go defaulted to
`GOMAXPROCS=1` until 1.5. Parallelism was never what made channels work. A goroutine parked on
`ch <- v` is suspended in the same basic sense as a generator parked at `yield`.

**Blocking.** Both suspend. Both resume when the counterparty is ready.

Once those three are set aside, the remaining differences are small, enumerable, and — importantly —
*independent*. They are axes, not a dichotomy.

## Axis 1: how far the producer may run ahead of demand

Call it **depth**.

| | depth |
| --- | --- |
| bare async generator | 0 |
| unbuffered Go channel | 1 |
| buffered Go channel, capacity *n* | *n* |
| an eager buffering wrapper over a generator | *n* |

Depth 0 means the producer computes item *N+1* only once the consumer asks for it. Depth 1 means the
producer has *already computed* item *N+1* and is parked offering it — it did that work while the
consumer was still busy with item *N*.

This is measurable rather than notional. A source and a sink with 50 ms of work each, three items:

```
compute 0 @0.00   consume 0 @0.05
compute 1 @0.10   consume 1 @0.15
compute 2 @0.20   consume 2 @0.26
total 0.31s
```

Fully serialized. Producing and consuming never overlap, because between hand-offs the producer is
not slow — it is *not running*.

**The key identity:** depth > 0 requires the producer to be scheduled by something other than the
consumer's `anext()`. You cannot buy lookahead without an independently-running producer, because
somebody has to advance the producer while the consumer isn't asking. So "how much lookahead" and
"who schedules the producer" are not two axes. They are one axis seen from either end.

That collapse is the useful part of this document.

## Axis 2: arity

| | producers | consumers |
| --- | --- | --- |
| async generator | 1 (its own body) | 1 (private handle) |
| channel | *n* | *m* |

A generator is a private handle, not a rendezvous point. Two consumers sharing one do not each see
the stream — they split it. And concurrent `anext()` on a single generator is not a queue, it is an
error:

```
RuntimeError: anext(): asynchronous generator is already running
```

Unlike depth, arity is not something a buffer fixes. It needs a different object: a broadcast/tee
for consumer arity, a pushable adapter for producer arity.

## Axis 3: what kind of handle you hold

A generator handle is **consumable**: the only verb is "give me the next item." A Go channel value
is both ends at once (idiomatically split with the directional types `chan<-` and `<-chan`).

We have no producible handle at all. There is no `await gen.push(x)` — to produce, you must be
*inside* the generator body, where the control inversion means you don't produce on your own
volition; you produce because you were asked to. That asymmetry is why unix `select` has a concept
we lack: it selects on writable descriptors as well as readable ones. Today a turbopipes select can
only ever watch the read side, because the write side is not a thing you can hold.

## Axis 4: completion and error semantics

This is the axis where generators are strictly richer, and it is worth naming because it is the
strongest argument against "just use a `Queue`."

| | end-of-stream | error propagation | teardown |
| --- | --- | --- | --- |
| async generator | `StopAsyncIteration` | raises at the consumer, traceback intact | `aclose()` / `GeneratorExit`, `finally` runs |
| Go channel | `close(ch)` | none — by convention, send an error value | none; goroutine leaks unless told to stop |
| `asyncio.Queue` | none at all | none | none |

A `Queue` has no way to say "that was the last one" and no way to say "I failed." Every Queue-based
pipeline reinvents both, usually with a sentinel value and a side-channel, and usually incorrectly
once there is more than one consumer.

## The thesis

Put the four together and the ownership property turbopipes leans on stops looking like a separate
feature:

> **An async generator has an owner by construction *because* it is depth 0.**

A producer that cannot run except when pulled cannot outlive its consumer. Closing the consumer is
sufficient to close the source, and that is why the whole set tears down through one handle instead
of through a `done`-channel protocol every participant must honour.

Which means the price list is fixed in advance, and it is the same price every time:

> **Every move to depth > 0 introduces an independently scheduled producer, which is a thing that
> can outlive its consumer, which is exactly the entity that needs supervision.**

So buffering is not a free ergonomic win to be bolted on. It re-imports Go's teardown problem in
proportion to how much of it you use. That does not make it wrong — it makes it a *trade to be
priced*, and it tells us what any buffering primitive has to prove: that the pump task it hides is
owned, cancelled, and finalized by the consumer handle it hands back.

## What this implies for the primitives we are contemplating

| Proposal | Axis it moves | Price |
| --- | --- | --- |
| one-shot `select` | none — same depth, same arity | none, if the armed pulls survive between calls |
| `abuffer(gen, n)` | depth 0 → *n* | a pump task to own, cancel and finalize |
| pushable producer handle | producer arity 1 → *n*; adds a producible handle | a task per producer; EOF and error must be re-invented |
| broadcast / tee | consumer arity 1 → *m* | per-consumer buffering, so depth as well |
| `Queue` interop | all of them, badly | loses EOF and error propagation (Axis 4) |
| actor | depends entirely on whether it has one output or many | see below |

The one entry that costs nothing is the first, which is a strong hint about where to start.

## The actor fork

An actor is a loop that reads from whichever inbound source is ready and emits outbound. In this
model that splits cleanly in two, and the split is the whole design decision:

- **Pipeline actor** — one output, and the output is *pulled*. It can be an ordinary async
  generator whose body loops over a per-item select. Depth 0 throughout, so ownership and teardown
  come free, and it composes with everything already in the library.
- **Graph actor** — many outputs, or an output that is written whether or not anyone is reading. It
  needs depth, therefore a task, therefore supervision.

Karl's phrasing — "a turbopipes actor would just be a loop that pulls items via per-item select" —
is exactly right for the first kind and describes only half of the second. The interesting question
is not whether the first is possible; it is whether the second can be built so that its supervision
is *also* reachable through one consumer handle, or whether it necessarily hands the user a
lifetime to manage.

That question is the reason to answer [0002](0002-single-item-select.md) first. If a per-item
select is genuinely free, then pipeline actors are available immediately with no new machinery, and
the graph-actor problem can be approached separately and honestly, rather than being smuggled in
underneath a convenience wrapper.
