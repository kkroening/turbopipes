# _Deriving Turbopipes from First Principles_ — the parts, and the measurements

The guide itself lives here, split into three parts, with an [index](../first-principles.md) one
directory up:

-   **[Part I — Deriving the pipeline](./part-1-the-derivation.md)** (§1–5)
-   **[Part II — Taking it apart](./part-2-taking-it-apart.md)** (§6–11)
-   **[Part III — The API, in hindsight](./part-3-the-api-in-hindsight.md)** (§12–14)

Everything else in this directory is a measurement script. Every measurement block in the three
parts is backed by one of them, and each script prints exactly the block it backs — so a number on
the page can be re-checked rather than taken on trust, and stays checkable when CPython or the
library moves. Checkable, not checked: see
[Nothing runs these automatically](#nothing-runs-these-automatically).

Run one from the repository root:

```console
$ python doc/first-principles/05_1_aclose_running.py
ag_running: True  ag_frame is None: False
aclose() raised: RuntimeError: aclose(): asynchronous generator is already running
  src finally ran
```

The scripts need no dependencies beyond the standard library and `turbopipes` itself; the ones
that import `turbopipes` add the repository root to `sys.path`, so they run from any working
directory.

## [Part I](./part-1-the-derivation.md) — deriving the pipeline

| § | Block | Script |
| --- | --- | --- |
| 2 | chunked `gather` vs. `aparallel`: wall clock and window occupancy | [`02_chunk_barrier.py`](02_chunk_barrier.py) |
| 2.1 | `gather` raises and leaves the peers running | [`02_1_orphaned_peers.py`](02_1_orphaned_peers.py) |
| 3 | the consumer walks away; nobody tells the workers | [`03_walk_away.py`](03_walk_away.py) |
| 3 | the consumer merely dawdles; the pool runs away anyway | [`03_backpressure.py`](03_backpressure.py) |
| 3.1 | sync vs. async generator finalization at a `break` | [`03_1_asyncgen_finalization.py`](03_1_asyncgen_finalization.py) |
| 4.1 | pages fetched: async generator source vs. materialized list | [`04_1_source_io.py`](04_1_source_io.py) |
| 4.1 | how far a source can run ahead of a dawdling consumer | [`04_1_bounded_source.py`](04_1_bounded_source.py) |
| 4.2 | what `gather`, `TaskGroup` and `aparallel` do to a failing item's peers | [`04_2_peer_failure.py`](04_2_peer_failure.py) |
| 4.3 | what leaving an `aparallel` loop early under `aclosing` does today | [`04_3_early_exit_today.py`](04_3_early_exit_today.py) |
| 5 | the naive merge, closed source-by-source, with sources mid-pull | [`05_naive_merge_teardown.py`](05_naive_merge_teardown.py) |
| 5.1 | `aclose()` on a generator suspended inside its own body | [`05_1_aclose_running.py`](05_1_aclose_running.py) |
| 5.1 | the consumer's exception, lost on the way out of the cleanup | [`05_1_lost_exception.py`](05_1_lost_exception.py) |
| 5.2 | cancellation reaches what `aclose()` cannot | [`05_2_cancel_reaches.py`](05_2_cancel_reaches.py) |
| 5.2 | the two-phase teardown, hand-rolled and via `aselect` | [`05_2_two_phase.py`](05_2_two_phase.py) |
| 5.3 | `AsyncExitStack` runs every callback and keeps one failure | [`05_3_exit_stack.py`](05_3_exit_stack.py) |
| 5.4 | PEP 525 converts a `StopAsyncIteration` raised from the body | [`05_4_stopasynciteration.py`](05_4_stopasynciteration.py) |
| 5.5 | backpressure through the merge, re-arming after vs. before the yield | [`05_5_backpressure.py`](05_5_backpressure.py) |
| 5.6 | `gather(return_exceptions=True)` vs. `Task`'s step handler | [`05_6_return_exceptions.py`](05_6_return_exceptions.py) |

## [Part II](./part-2-taking-it-apart.md) — taking it apart

| § | Block | Script |
| --- | --- | --- |
| 7.1 | a failing source through `amerge` alone, and behind `ataskify` | [`07_1_amerge_alone.py`](07_1_amerge_alone.py) |
| 7.2 | service gap and reproducibility: `set` order vs. least-recently-served | [`07_2_round_robin.py`](07_2_round_robin.py) |
| 8.1 | awaiting the pull vs. waiting on it | [`08_1_wait_not_await.py`](08_1_wait_not_await.py) |
| 8.2 | head-of-line blocking from yielding a pull before it completes, and the backpressure that survives it | [`08_2_head_of_line.py`](08_2_head_of_line.py) |
| 9 | `atag` outside vs. inside `ataskify`, in the failure case | [`09_tag_placement.py`](09_tag_placement.py) |
| 10.1 | the hand-written composition against `aselect` | [`10_1_reassembly.py`](10_1_reassembly.py) |
| 10.2 | event-loop passes per delivered item: monolith vs. composition | [`10_2_loop_passes.py`](10_2_loop_passes.py) |
| 10.3 | what moved in the cross-source interleaving, and what didn't | [`10_3_interleaving.py`](10_3_interleaving.py) |
| 11.1 | removing each layer's settling, one at a time | [`11_1_who_settles.py`](11_1_who_settles.py) |
| 11.2 | settling nested inside the closes vs. outside them | [`11_2_nesting.py`](11_2_nesting.py) |
| 11.3 | a sequential close loop, nested `aclosing`, and `aclosing_all` | [`11_3_aclosing_all.py`](11_3_aclosing_all.py) |
| 11.4 | a cleanup failure reported with and without a `label` | [`11_4_label_seam.py`](11_4_label_seam.py) |
| 11.5 | the cleanup-failure report dropped by a second cancellation | [`11_5_report_gap_today.py`](11_5_report_gap_today.py) |

[Part III](./part-3-the-api-in-hindsight.md) quotes no measurements of its own — it attributes the
ones above to the layers that produce them.

## What is and isn't stable

The parts quote these outputs on **CPython 3.14.6**. Thirty of the thirty-one are exact: where a
measurement could be driven by a stopwatch or by counting event-loop passes, these scripts count
event-loop passes, so the number is a property of the design rather than of the machine it ran on.

Two different things can make a script's output move, and only one of them is about reproducing it
today. Both are labelled on the page.

**One moves between runs:**

-   [`02_chunk_barrier.py`](02_chunk_barrier.py) is a wall-clock benchmark. Its timings are
    dominated by `asyncio.sleep`, so they reproduce closely, but the mean-occupancy figures are
    sampled and their last digit moves between runs.

**Two will move when the library does.** Both are byte-for-byte reproducible on demand; what they
are pinned to is a defect, so it is fixing the defect — not re-running the script — that changes
them:

-   [`04_3_early_exit_today.py`](04_3_early_exit_today.py) measures the `aparallel` teardown defect
    rather than a design, and is expected to change when that defect is fixed. See the footnote in
    [Part I](./part-1-the-derivation.md).
-   [`11_5_report_gap_today.py`](11_5_report_gap_today.py) likewise, for a different defect — a
    cleanup-failure report dropped when a second cancellation lands inside `asettle`'s own gather.
    See the footnote in [Part II](./part-2-taking-it-apart.md).

Two further scripts — [`04_1_source_io.py`](04_1_source_io.py) and
[`04_1_bounded_source.py`](04_1_bounded_source.py) — leave an `aparallel` loop early, and so trip
over that same `aparallel` teardown defect on the way out. Each swallows exactly the
`BaseExceptionGroup` of `GeneratorExit` it produces, re-raising anything else, and says so where it
does it. The measurements themselves are unaffected: the cleanup runs correctly, it just also
raises.

One more is worth a note even though it does reproduce.
[`07_2_round_robin.py`](07_2_round_robin.py) reports, among other things, what an *arbitrary* order
does — set iteration order over `Task` objects, which is address order and differs on every run. Its
two summary figures for that row are stable because they are aggregates over twenty trials rather
than properties of one, and twenty trials reliably find the worst case; the underlying orders they
summarise are different every time, which is exactly the point that row is making.

## Nothing runs these automatically

`scripts/ci` runs mypy, pytest, pylint, isort and black, and each one is confined to `turbopipes`
and `tests` — isort and pylint by the paths they are given, mypy by `files`, pytest by
`testpaths`, black by its `include` regex. None of them is pointed at `doc/`, so nothing re-runs
these scripts, and nothing compares what they print against what the parts quote. The
correspondence described at the top of this file is hand-checked: a number on the page carries a
guarantee that somebody ran the script, not one that anything will notice when it stops matching.

Making it an enforced guarantee would be worth doing — thirty of the thirty-one scripts are
deterministic, so a check that ran each one and asserted its output appears verbatim in the part it
belongs to would be a real assertion rather than a smoke test for all but one of them.

Comparing stdout alone would not be enough, though, and it comes up short in two opposite
directions — both of which involve the `aparallel` teardown defect, and only one of which is
visible from the output:

-   [`04_1_source_io.py`](04_1_source_io.py) and [`04_1_bounded_source.py`](04_1_bounded_source.py)
    swallow the `BaseExceptionGroup` on the way out and exit `0` with empty stderr, so the defect
    never reaches their stdout. A stdout-only check passes today and would go on passing unchanged
    once the defect is fixed, registering neither state. These want an expected **exit shape** as
    well.
-   [`04_3_early_exit_today.py`](04_3_early_exit_today.py) is the reverse: it catches the escaping
    exception and prints it, so its *stdout* is what encodes the defect. A stdout-only check would
    start **failing** there the moment the defect is fixed. This one wants an
    **expected-to-change** marker, not a tighter assertion — and
    [`11_5_report_gap_today.py`](11_5_report_gap_today.py) wants the same marker, for the same
    reason.

Whether such a check belongs in this repository's `tests/`, or somewhere of its own, is a
question about what that suite is for rather than one this directory should answer on its own, and
it is left open as a follow-up for the maintainer.
