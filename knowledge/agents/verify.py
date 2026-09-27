"""Check each claim against the span it cites.

This has to be a separate pass from the one that proposed the claim. A model grading
its own output confirms what it already believes — that is the circularity the whole
corpus exists to break, and it is not broken by asking nicely in the same prompt.

The job here is narrow on purpose: not "is this true?" but "does this span say this?".
Faithfulness is checkable; truth is an argument. Anything that fails, or that the
verifier is unsure about, goes to a human — and that set should be small enough to read.
"""
from __future__ import annotations

from base import Agent, listy


class Verify(Agent):
    name = "verify"
    table = ""            # updates in place rather than inserting
    workers = 4

    schema = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"fact_id": {"type": "integer"},
                       "supported": {"type": "boolean"},
                       "reason": {"type": "string"}},
        "required": ["fact_id", "supported"]}}}, "required": ["verdicts"]}

    def targets(self):
        rows = self.store.db.execute(
            "SELECT f.id, f.claim, f.detail, s.text AS span_text "
            "FROM fact f JOIN span s ON s.id = f.span_id "
            "WHERE f.status = 'candidate'").fetchall()
        return [list(rows[i:i + 10]) for i in range(0, len(rows), 10)]

    def prompt(self, batch):
        items = [f"[fact {r['id']}]\n  claim: {r['claim']}\n"
                 f"  detail: {r['detail'] or '—'}\n  cited span: {r['span_text'][:900]}"
                 for r in batch]
        return (
            "For each fact, decide one thing only: does the cited span actually support "
            "the claim?\n\n"
            "Not whether the claim is true in general — whether this span says it. "
            "A number that differs, a condition dropped, a hedge turned into a "
            "certainty: all unsupported. If the span is about something else, "
            "unsupported.\n\n"
            "Be strict. A fact wrongly passed is one a student will be taught.\n\n"
            + "\n\n".join(items))

    def rows(self, batch, result):
        ids = {r["id"] for r in batch}
        n = 0
        for v in listy(result, "verdicts"):
            fid = v.get("fact_id")
            if fid not in ids:
                continue
            self.store.db.execute(
                "UPDATE fact SET status=?, verdict=? WHERE id=?",
                ("verified" if v.get("supported") else "rejected",
                 (v.get("reason") or "")[:400], fid))
            n += 1
        self.store.db.commit()
        return []
