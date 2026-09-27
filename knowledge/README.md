# Knowledge layer

The chemistry syllabus as something a tutor can be held to: what is true, how a question
is attacked, what links two chapters, and what students believe instead.

    python3 knowledge/build.py ingest --sources sources/unpacked   # no model needed
    python3 knowledge/build.py agents --only kech103 --chapter "Periodic Classification"
    python3 -m pytest knowledge/tests -q

## Build and runtime are different machines

**Build** roams. An agent can re-read, check itself, and take an hour; a failed batch is
simply run again. **Runtime** does not: a student is waiting, and the model call already
costs ~2s to first audio. So the corpus is produced offline and shipped as a versioned
SQLite file inside the container — one file, no service, no hop, no timeout path in the
middle of an answer.

Retrieval at request time is a local query over a few thousand spans: single-digit
milliseconds, against a model call three orders of magnitude slower. Which is why the
earlier design — baking one chapter's facts into each lesson — was wrong. It bought a
latency saving that did not exist and paid for it in scope: a student asking how this
connects to thermodynamics got *"yeh is chapter mein nahi hai"*, which is a tutor with
amnesia. **Runtime queries the whole syllabus.**

## Six kinds of knowledge, not one

A textbook hands over facts, and facts are the least of what a tutor needs.

| | |
|---|---|
| `fact` | a checkable claim, with the span it came from |
| `method` | how a question is attacked — trigger, steps, the step people get wrong |
| `derivation` | why a result holds, so a student can rebuild it |
| `connection` | what links two concepts across chapters |
| `misconception` | what students believe instead, mined from distractors |
| `figure` | what has to be drawn |

`method` is what coaching material has and a textbook does not. A tutor that can say
*"this is a Nernst-with-concentration-cell question and the trap is the sign"* is doing
something no fact retrieval can.

`connection` is the answer to cross-chapter questions — extracted from sources that
already draw those links, rather than a prerequisite graph nobody can validate without
learner data.

## Notation is the hard part, and it is solved for free

`pypdf` produces **zero** unicode sub- or superscripts across these sources. `[Ag(CN)₂]⁻`
arrives as `2Ag CN`; `E°(Zn²⁺,Zn)` as three fragments on separate lines. A model reading
that cannot tell it is wrong, and neither can you.

The information was never missing. A PDF records each run's font size and baseline, and
a run about a third smaller than its line's body text is a sub- or superscript depending
on which side of the baseline it sits. Rebuilding from that recovers **372**
sub/superscripts across 72 NCERT pages and **836** across 71 sheet pages —
deterministically, no model, no network.

What it does not fix is stacked notation, where the script is its own line object rather
than a run inside one. Those pages flag themselves (`page.needs_vision`) and are the
only ones needing a vision pass: **8% of NCERT**, 58% of the question sheets.

## The agents

Separate, rather than one large prompt, so a bad batch can be rerun alone, each agent
can be judged on its own output, and — most importantly — the verifier is a different
pass from the proposer. A model grading its own work confirms what it already believes.

    concepts  ->  facts  ->  verify
                  methods
                  connections
                  misconceptions

`verify` asks one narrow question: *does the cited span actually say this?* Not whether
the claim is true — faithfulness is checkable, truth is an argument. Whatever fails, or
it is unsure about, goes to a human, and that set is small enough to read.

## Not tied to a provider

`SAARTHI_KB_PROVIDER = gemini | anthropic | stub`. The build is offline batch work with
no latency budget, so there is no reason to depend on one vendor and one good reason not
to: this project lost model access mid-build and everything stopped. `stub` returns
empty results of the right shape, so the pipeline, batching and run log can be exercised
and tested without a key or a bill.

## Where it stands

Ingested, deterministically, with no API: **132 sources, 2,735 pages, 19,156 spans,
16,338 questions**. NCERT chemistry alone is 696 pages and 3,144 spans, median 548
characters — small enough that checking a citation takes seconds.

The agents are built and the plumbing is proven end to end on `stub`. They have not yet
run against a real model, because the project's Gemini access is currently denied.
