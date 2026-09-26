# Knowledge layer

The syllabus as retrievable, citable text — NCERT Chemistry and our own notes — so a
fact sheet can be checked against a source instead of against a model's confidence.

Nothing here yet. It is a boundary, not a placeholder: when the corpus arrives it plugs
in at one place, and everything else stays where it is.

## What it is for

The lesson pipeline writes a fact sheet; the classroom grounds every answer on it. Today
both are produced and checked by the same model, which makes the check circular — a
model grading its own claims confirms what it already believes. The E/B geometry error
in ML1.1 passed every eval and was caught by a person.

So the first job here is not generation, it is **verification**: every claim in a fact
sheet must cite a retrievable span. Claims that ground cleanly pass; the ones that do not
are exactly where the errors are, and they are the only ones a human needs to read.

## Contract

Two functions, so the rest of the system never learns how retrieval works:

    search(query, *, chapter=None) -> [Span(text, source, chapter, page, score)]
    ground(claim)                  -> Span | None

`factory/pipeline/` calls `ground()` before writing a manifest. `classroom/` may call
`search()` to cite a source when a student pushes back on an answer.

## Decided before any code

* **NCERT plus our own notes only.** Third-party textbooks are someone else's copyright,
  and "it is only for retrieval" is not the protection people assume. Every claim
  traceable to the prescribed syllabus is also a better pitch than "our AI knows this".
* **Chemistry extracts badly.** Subscripts, charges, reaction arrows and structures are
  where the meaning lives and where PDF-to-text fails silently. Formulas keep their
  LaTeX; structures stay images and are retrieved with vision. A corpus that is
  confidently wrong about H₂SO₄ is worse than no corpus.
