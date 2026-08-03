# Deriving Turbopipes from First Principles

### _Why async is harder than it looks_

The `turbopipes` API looks slightly arbitrary the first time you meet it. It insists on an async
generator rather than any old iterable. It hands you `asyncio.Task` objects instead of the results
you asked for. Its documentation nags you about `contextlib.aclosing` on nearly every example. And
for merging several streams it offers four functions where you expected one.

None of that is taste. Each of those decisions is the answer to a specific way that the obvious
designs fall over — usually not on the happy path, but at the moment a consumer stops consuming.
This guide walks that road twice: first deriving a pipeline from the designs everyone writes before
it, and then taking the result apart to find out which of it you were actually using.

It is in three parts, meant to be read in order.

## [Part I — Deriving the pipeline](./first-principles/part-1-the-derivation.md)

**§1–5.** The problem, and the two designs everyone writes first: `asyncio.gather()` in chunks, and
a hand-rolled `asyncio.Queue` worker pool. Each is run into a consumer that stops early, and what
survives is `aparallel` for fanning one stream out and a merge for fanning several streams in. Ends
with the teardown that merging forces — cancel the in-flight pulls, *then* close the sources — and
with why an `aclose()` arriving in the wrong order eats the exception that caused it.

Start here. Everything after it is a consequence.

## [Part II — Taking it apart](./first-principles/part-2-taking-it-apart.md)

**§6–11.** Part I's merge is four separable jobs wearing one function's name: interleaving,
isolating failure, attaching identity, and tearing down. This part separates the first three into
`amerge`, `ataskify` and `atag`, asks each what it does *alone*, puts them back together into
`aselect`, and adds up the bill — six event-loop passes per delivered item against three, and a
cross-source interleaving that moved. It ends on the cleanup pair `asettle` and `aclosing_all`: why
every layer that arms a pull has to settle its own, why the two nest one way round only, and the one
seam the decomposition couldn't cut cleanly.

## [Part III — The API, in hindsight](./first-principles/part-3-the-api-in-hindsight.md)

**§12–14.** The whole surface in one place, with each property attributed to the layer that actually
provides it rather than to the function you happened to call — and a good deal of it turning out to
be things a layer deliberately refuses to do on your behalf.

## The measurements

Every measurement block in the three parts is backed by one script in
[`first-principles/`](./first-principles/), and each script prints exactly the block it backs. A
number on the page can therefore be re-checked rather than taken on trust, and stays checkable when
CPython or the library moves.

The figures quoted were produced on **CPython 3.14.6**. Thirty of the thirty-one scripts are
byte-for-byte reproducible on demand; the exception is one wall-clock benchmark, whose sampled
figures move in the last digit. Separately, two scripts measure a *defect* rather than a design, and
are expected to change when those defects are fixed — which is a promise about the future rather
than about reproducing today. All three are called out where they appear, and
[`first-principles/README.md`](./first-principles/README.md) carries the full index and the
stability notes.

If you don't believe a claim, run it:

```console
$ python doc/first-principles/05_1_aclose_running.py
ag_running: True  ag_frame is None: False
aclose() raised: RuntimeError: aclose(): asynchronous generator is already running
  src finally ran
```

---

**Back to** [the README](../README.md) **·** start reading at
[Part I](./first-principles/part-1-the-derivation.md).
