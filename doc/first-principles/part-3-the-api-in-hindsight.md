# Part III — The API, in hindsight

### _Which layer is actually paying for what_

**[Index](../first-principles.md)** **·**
[Part I — the derivation](./part-1-the-derivation.md) **·**
[Part II — taking it apart](./part-2-taking-it-apart.md) **·** Part III

Two parts of derivation, and the API that looked slightly arbitrary on
[the way in](../first-principles.md) has stopped looking that way. What is left is to say who is
responsible for what — because after [Part II](./part-2-taking-it-apart.md) the answer is rarely
"the function you called".

---

## 12. Who owns what

Seven exports carry the material this guide derives. Fan-out is one function; fan-in is four, three
of which are the merge taken apart and one of which is sugar over those three; and the teardown is
two primitives shared between them.

| | What it is | What it owns | What it deliberately doesn't do |
| --- | --- | --- | --- |
| **`aparallel`** | fan-out: one stream across many tasks | the rolling window, the source generator, and cancelling the window on the way out | interpret an item's failure — it hands you the task ([§4.2](./part-1-the-derivation.md#42-why-it-yields-awaitables-instead-of-results)) |
| **`amerge`** | fan-in: many streams into one, in completion order | one pull per source, the round-robin tie-break, and settling *its own* pulls before closing its sources | know anything about keys or tasks; a failing source ends the merge ([§7.1](./part-2-taking-it-apart.md#71-what-it-does-alone--and-what-it-therefore-cant-do)) |
| **`ataskify`** | one source's pulls, delivered as completed tasks | exactly one pull, settled before its source is closed; the source's cleanup-failure report | decide what a failure means — that is the consumer's ([§8](./part-2-taking-it-apart.md#8-ataskify-whose-failure-is-it)) |
| **`atag`** | constant identity attached to a stream | nothing but closing the generator it wraps | arm a pull — which is why it needs no settling of its own ([§11.1](./part-2-taking-it-apart.md#111-every-layer-that-arms-a-pull-settles-its-own)) |
| **`aselect`** | keyed fan-in | nothing at all — it is one expression over the three above | exist as a separate mechanism; reach past it when it doesn't fit ([§10.1](./part-2-taking-it-apart.md#101-the-composition-is-the-function)) |
| **`asettle`** | cancel pulls and wait for them | absorbing every outcome, and reporting a cleanup failure nobody can receive | raise — it runs where there is no consumer left to raise to ([§11.4](./part-2-taking-it-apart.md#114-the-seam-naming-a-failure-nobody-can-receive)) |
| **`aclosing_all`** | bulk `contextlib.aclosing` | closing every generator, however the block is left | collect more than one close failure ([§11.3](./part-2-taking-it-apart.md#113-what-aclosing_all-buys-and-what-it-doesnt)) |

The column that repays reading twice is the last one. Most of what makes this library work is
things a layer refuses to do on your behalf: `aparallel` and `ataskify` refuse to interpret a
failure, `amerge` refuses to know what a key is, `asettle` refuses to raise, `aselect` refuses to be
a mechanism. Each refusal is a decision that stayed with the caller because the caller is the only
one with the context to make it.

## 13. The bits that look arbitrary

Each of these is a receipt, and each is now attributable to a specific layer rather than to a
function's reputation:

| The bit that looks arbitrary | Which layer pays for it | What it's actually paying for |
| --- | --- | --- |
| Takes an **async generator**, not an iterable | every source, everywhere | the source's own I/O is still undone when the pipeline starts, so there is something left for backpressure to withhold — a list has already paid for all of it ([§4.1](./part-1-the-derivation.md#41-why-the-input-is-an-async-generator-not-a-list)) |
| Yields **awaitables**, not results | `aparallel` fanning out; `ataskify` fanning in | one item's failure is the consumer's to interpret, not the pipeline's to act on ([§4.2](./part-1-the-derivation.md#42-why-it-yields-awaitables-instead-of-results), [§8](./part-2-taking-it-apart.md#8-ataskify-whose-failure-is-it)) |
| …but `amerge` yields **values** | `amerge`, by omission | a merge with no failure policy of its own inherits `async for`'s, which is right when the sources are facets of one thing — and is opt-out by wrapping them ([§7.1](./part-2-taking-it-apart.md#71-what-it-does-alone--and-what-it-therefore-cant-do)) |
| Results come out in **completion order** | `aparallel`'s window; `amerge`'s `FIRST_COMPLETED` | no chunk barrier, so a straggler costs one slot rather than the whole window ([§2](./part-1-the-derivation.md#2-attempt-one-asynciogather-in-chunks)) |
| Ties broken **least-recently-served** | `amerge` | `asyncio.wait` returns a `set`, so the obvious loop serves in address order: not reproducible, and a service gap of `2n - 1` rather than `n` ([§7.2](./part-2-taking-it-apart.md#72-the-tie-break-the-obvious-merge-doesnt-have)) |
| Pairs with **`aclosing`** | every generator the library returns | async generator cleanup is explicit, and closing one has to be awaited, so only the code that stopped consuming can do it ([§3.1](./part-1-the-derivation.md#31-async-generator-cleanup-is-not-the-forgiving-thing-youre-used-to)) |
| **Cancels before it closes** | `amerge` and `ataskify`, each for its own pulls | `aclose()` cannot touch a source suspended inside its own body — and fails loudly from the cleanup path when you try ([§5.1](./part-1-the-derivation.md#51-aclose-will-not-touch-a-generator-thats-inside-its-own-body), [§5.2](./part-1-the-derivation.md#52-what-does-reach-a-running-generator-cancellation)) |
| …and the settling is **nested inside** the closes | `aclosing_all` as a context manager | ordering that survives the cleanup path itself being interrupted, rather than ordering by statement sequence ([§11.2](./part-2-taking-it-apart.md#112-the-nesting-is-not-optional)) |
| `ataskify` **waits** on its pull rather than awaiting it | `ataskify` | awaiting re-raises the source's failure as `ataskify`'s own, which is the coupling the layer exists to remove ([§8.1](./part-2-taking-it-apart.md#81-wait-on-the-pull-dont-await-it)) |
| …and yields it only once **complete** | `ataskify` | a generator parked at its `yield` is instantly ready, which collapses a merge's completion order into arming order and reinstates head-of-line blocking ([§8.2](./part-2-taking-it-apart.md#82-why-the-pull-has-to-finish-before-its-yielded)) |
| `atag` goes **outside** `ataskify` | `aselect`'s composition order | the key has to be readable before the `await`, because the `await` is what may raise instead of returning it ([§9](./part-2-taking-it-apart.md#9-atag-three-lines-and-one-decision)) |
| `amerge` re-arms **after** the yield | `amerge` | halves the merge-wide produced-but-unconsumed figure, from two items to one; the per-source bound of one holds either way ([§5.3](./part-1-the-derivation.md#53-backpressure-survives-the-merge)) |
| Exhaustion carries **no sentinel** | `is_exhausted`, used by both `amerge` and `ataskify` | PEP 525 makes a `StopAsyncIteration` on a pull unambiguous, so the yielded type stays honestly `Task[T]` ([§7](./part-2-taking-it-apart.md#exhaustion-doesnt-need-a-sentinel)) |
| `ataskify` takes a **`label`** it only logs | `ataskify`, fed by `aselect` | the layer that can see a source's cleanup failure is not the layer that knows its name, and the failure reaches no consumer ([§11.4](./part-2-taking-it-apart.md#114-the-seam-naming-a-failure-nobody-can-receive)) |
| `aclosing_all` exists at all | `aclosing_all` | not a stronger guarantee than nested `aclosing` — dynamic arity, which nested blocks can't express ([§11.3](./part-2-taking-it-apart.md#113-what-aclosing_all-buys-and-what-it-doesnt)) |
| `asettle` **absorbs** every outcome | `asettle` | it runs on a path that is already unwinding; raising would displace the exception doing the unwinding ([§11.4](./part-2-taking-it-apart.md#114-the-seam-naming-a-failure-nobody-can-receive)) |

## 14. Where to read the code

The implementations carry their reasoning in the docstrings, at more length than is usual, for the
same reason this document exists — most of these decisions look like preferences until you know what
they cost.

-   [`_aparallel.py`](../../turbopipes/_aparallel.py) — the fan-out
    ([§4](./part-1-the-derivation.md#4-deriving-aparallel))
-   [`_amerge.py`](../../turbopipes/_amerge.py) — the fan-in
    ([§7](./part-2-taking-it-apart.md#7-amerge-interleaving-and-nothing-else))
-   [`_ataskify.py`](../../turbopipes/_ataskify.py) — the pull-to-task wrapper
    ([§8](./part-2-taking-it-apart.md#8-ataskify-whose-failure-is-it))
-   [`_atag.py`](../../turbopipes/_atag.py) — identity
    ([§9](./part-2-taking-it-apart.md#9-atag-three-lines-and-one-decision))
-   [`_aselect.py`](../../turbopipes/_aselect.py) — the one-expression composition
    ([§10.1](./part-2-taking-it-apart.md#101-the-composition-is-the-function))
-   [`_aclosing.py`](../../turbopipes/_aclosing.py) — `asettle` and `aclosing_all`
    ([§11](./part-2-taking-it-apart.md#11-the-cleanup-extracted-asettle-and-aclosing_all))

---

None of this is exotic. It is what's left after you take the two designs everyone writes first, run
them into a consumer that stops early, refuse to look away from what happens next — and then take
the answer apart to see which of it you were actually using.

---

**Back to** [the index](../first-principles.md) **·** back to
[Part II — taking it apart](./part-2-taking-it-apart.md) **·** the scripts behind every measurement
in this guide are in [this directory](./README.md) **·** and
[the README](../../README.md) is where the library starts.
