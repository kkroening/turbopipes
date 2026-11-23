# Issue tracker guide

A lightweight, **in-repo** issue tracker: one Markdown file per issue, under
[`docs/issues/`](issues/). It exists so that feature requests, bugs, and design
questions have a durable home that travels with the code — and so the reasoning
behind a change survives longer than a chat log.

This is turbopipes' issue tracker. GitHub Issues is not used; the repo is the
tracker. A tracked issue is therefore reviewable, diffable, and greppable
alongside the code it describes, and arrives with every clone.

[`docs/issues/README.md`](issues/README.md) is the tracker's landing page — a
generated at-a-glance index — while this file is the conventions guide.

> **Provenance.** These conventions are adapted from a private template shared
> across the author's other repos. Because turbopipes is public and that
> template is not reachable from here, this file is a **vendored copy that is
> maintained in this repo**: fix it here, and don't assume an upstream will
> propagate a change either way.

## Conventions

-   **One file per issue**, named **`NNNN.md`** (zero-padded, monotonic —
    number only, no title slug). Titles are mutable and live in the file's `#`
    heading and the README index; keeping them out of the filename means
    retitling an issue never breaks a link.
-   **Title the issue by its fix, not its symptom.** When a direction is
    proposed, name the issue for the **proposed fix or improvement**
    (*Stop clearing the task's traceback flag*, not *Failures are silent*) so
    the index reads as a list of intended changes. Fall back to naming the
    **problem** only when no fix is proposed yet — a raw bug report or an open
    question with no direction. Titles are mutable (above), so **retitle from
    problem to fix** once an issue gains a direction. (The summary blockquote
    still opens with the *problem* — see
    [How to write a good issue](#how-to-write-a-good-issue); the title says
    what we'll do, the summary says why.)
-   Each file opens with a **Status · Kind · Parent · Related** header (it
    renders as one line; see the cross-link form below), then a **summary
    blockquote** and (for anything non-trivial) a `## Background` section —
    see [How to write a good issue](#how-to-write-a-good-issue) — then
    `## Proposal` / `## Acceptance` as needed.
-   **Status:** `Open`, `In progress`, `Resolved` (with a short
    `## Resolution` note), `Rejected` (kept for historical record, with a note
    explaining why it won't be pursued), or `Superseded by NNNN` (kept for
    history; a lead note at the top says what replaced it and why). The status
    may carry a short trailing parenthetical (e.g.
    `In progress (implemented in [#12](…))`); the generated index shows just
    the bare status.
-   **Flip the status in the PR that changes it, not a follow-up.** The PR that
    resolves an issue should itself move the status to `Resolved` (and add the
    `## Resolution` note), so the moment it merges, `main` is already correct —
    the status change is part of the work, not after-the-fact bookkeeping.
    Since the house merge style is squash-and-merge, there's no window to "fix
    it up later" cleanly: a separate status-only PR is a smell. Only use
    `In progress` when the PR genuinely isn't the last piece. After the flip,
    regenerate the index (below).
-   **Kind:** `Enhancement`, `Bug`, or `RFC` — three, so that choosing is quick
    and the index stays comparable:
    -   **`Enhancement`** — nothing is broken; the issue asks for something
        more. It covers **new capability just as much as refinement of existing
        behaviour**, so "is this a feature or an improvement?" is never a
        question you have to answer.
    -   **`Bug`** — something is broken *today*: an invariant asserted
        somewhere and not holding, a documented behaviour that doesn't happen,
        a document that is wrong or incomplete about its own subject.
    -   **`RFC`** — the output wanted is a *decision*, not a diff: a proposed
        direction to argue, or an open design question with no direction yet.
        Once one is settled, the work it implies is filed as `Enhancement`s.
-   **Parent:** every issue links back in its header — either to a parent issue
    when it's a sub-issue of a larger effort, or, for a parentless issue, to
    the tracker landing page (`Parent: Issues`). (Rendered as a hover-title
    anchor — see the cross-link form below.)
-   **Header cross-links carry hover titles.** The `Parent` and `Related` links
    in the header are `<a href title>` anchors whose `title` attribute is the
    **target's `#` heading, verbatim** (the whole `NNNN — <title>`, backticks
    and all), so hovering the bare number on rendered GitHub shows what the
    issue *is* without navigating away. HTML-escape only the four
    HTML-sensitive characters if a title ever contains one (`"`→`&quot;`,
    `&`→`&amp;`, `<`→`&lt;`, `>`→`&gt;`); everything else is copied raw. In the
    **source**, the header soft-wraps for clean diffs — the `**Status:**` line
    then one `·`-led line per field, and one `Related` issue per line — which
    Markdown joins back into the single rendered header line (so never end a
    header line with two trailing spaces or a backslash, which would force a
    hard break). This applies to the **header only**; cross-links in the body,
    the index, and other docs stay concise `[NNNN](NNNN.md)` markdown, where
    surrounding prose already supplies the context. Captured titles are
    snapshots — if a target is retitled, refresh stale tooltips
    opportunistically.
-   **The README index is generated — don't hand-edit it.** After adding,
    retitling, or restatusing an issue, run **`scripts/render_issues`** (from
    the repo root) to regenerate the index from the issue files; it lists them
    in **descending order by number** (newest first).
    `scripts/render_issues --check` verifies the index is current without
    writing — it runs in CI — and doubles as a format linter: it fails on an
    issue whose heading or `Status`/`Kind` can't be parsed.

## How to write a good issue

The goal: a reader who has *never seen this project* can grasp the problem in
the first few sentences, and a reader who needs the details can get them
without wading through preamble. Most of this is proof-in-the-pudding —
**imitate the issues in the repo that already do it well** — but the
principles:

1.  **Summary first, and make it a real summary (1–3 sentences to the heart).**
    The opening blockquote must state *the actual problem*, not the history
    leading up to it — and in terms a newcomer understands. Think of it like a
    Python docstring: the first line or two get straight to what's wrong and
    why it hurts; optional follow-up sentences add just enough shape. A good
    tell: does sentence one convey why someone should care, and hint at the
    cause, without assuming any project vocabulary? If it spends three
    sentences on background and never lands the problem, it's the worst of both
    worlds — rewrite it.
    -   **Format the blockquote as mini-paragraphs** (blank `>` lines between
        them): the opening explanation stands alone as its own first paragraph,
        and each follow-up thought — the consequences, the proposed direction —
        gets its own. A one-glance structure beats one dense block, even when
        each paragraph is a single sentence.

2.  **Don't rely on project jargon defined "on the other side of the repo."**
    Terms like *arming a pull*, *taskified source*, or *0-deep* are fine **once
    introduced**, but the summary can't assume them. Either avoid them up top
    or gloss them in-line ("start the next pull before anyone asks for it — the
    step this codebase calls *arming*"). The reader shouldn't have to open
    three other issues to parse sentence one.

3.  **Set the stage with a `## Background` section — and make it concrete.**
    Right after the summary (not as an appendix), show the problem *in its
    natural habitat*, in whatever form makes it land fastest: a short code
    snippet, real captured output, a compact timeline of the flow, a small
    before/after contrast. The point is to introduce the vocabulary by
    *showing* it and give the reader something concrete to hold before any
    abstract prose — walls of text are where the actual problem goes to hide. A
    particularly effective shape when it fits: **working case → broken case →
    workaround**. Snippets are **illustrative** — trim to the point, label them
    `(sketch)`/`(abridged)`, and don't feel obliged to keep them 100% accurate
    as the code evolves; a slightly-stale snippet that teaches beats a precise
    wall of text.
    -   **The background stands on its own — the summary is not its prologue.**
        Open by establishing the setting from scratch (what the relevant piece
        of the system does, the concepts in play, why the concern exists at
        all) *before* developing the problem — don't resume mid-argument as if
        the summary were paragraph one, and don't jump the gun by putting the
        conclusion in the heading (`## Background`, not
        `## Background — why X can't work`). The summary is the pitch; the
        background is where understanding is actually built.
    -   **If the issue is about a flow or pipeline** (input consumed → steps →
        output produced), lead with the *input* and walk the flow in order, so
        the reader sees where each artifact sits — not an output in isolation.

4.  **Prefer measured output to assertion.** This is a concurrency library, and
    almost every interesting claim about it is timing-dependent. An issue that
    pastes a real run — with a **control** showing the contrasting case — is
    worth several paragraphs of reasoning, and is what lets a reader disagree
    with you productively.

5.  **Then go deep.** After the problem is framed, add the fuller treatment —
    the complete list of cases, the proposed design, trade-offs, non-goals,
    acceptance criteria. Depth is good *here*; it's the ordering that matters
    (frame, then elaborate).

## Skeleton

A rough skeleton (adapt, don't cargo-cult):

```markdown
# NNNN — <the proposed fix/improvement; or the problem, if none is proposed yet>

**Status:** …
· **Kind:** …
· **Parent:** <a href="README.md" title="Issues">Issues</a>
· **Related:**
  <a href="NNNN.md" title="NNNN — <target's `#` heading, verbatim>">NNNN</a>

> <sentence(s) 1: the problem itself — its own mini-paragraph(s), no project jargon.>
>
> <why it hurts / the consequence — its own mini-paragraph(s).>
>
> <optional: the proposed direction or the key distinction.>

## Background
<the concrete framing: snippet / captured output / timeline / before-after>

## Proposal / The fix
## Non-goals
## Acceptance
```
