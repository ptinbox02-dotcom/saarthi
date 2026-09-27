#!/usr/bin/env python3
"""Ask the corpus what it knows. What the tutor will call at runtime.

    python3 knowledge/query.py "why does ionisation enthalpy dip at oxygen?"

Keyword and concept routing for now; a local embedding index slots in behind the same
three functions without anything upstream noticing. No chapter scoping — the whole
syllabus is in scope, because a student asking how this connects to something they met
two chapters ago deserves an answer rather than a refusal.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Store   # noqa: E402

STOP = set("what why how does the a an of is are in to and for with that this it if".split())


def _words(q: str) -> list[str]:
    return [w for w in re.split(r"\W+", q.lower()) if len(w) > 3 and w not in STOP]


def resolve(store: Store, question: str, limit: int = 4) -> list[dict]:
    """Which concepts is this about? Scored over name, summary and the facts beneath."""
    words = _words(question)
    if not words:
        return []
    rows = store.db.execute("SELECT id, name, summary, chapter, syllabus_seq FROM concept")
    scored = []
    for c in rows:
        hay = f"{c['name']} {c['summary'] or ''}".lower()
        score = sum(3 for w in words if w in hay)
        score += store.db.execute(
            "SELECT COUNT(*) n FROM fact WHERE concept_id=? AND ("
            + " OR ".join("lower(claim||' '||COALESCE(detail,'')) LIKE ?" for _ in words)
            + ")", [c["id"]] + [f"%{w}%" for w in words]).fetchone()["n"]
        if score:
            scored.append((score, dict(c)))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


def facts(store: Store, concept_ids: list[str], limit: int = 12) -> list[dict]:
    if not concept_ids:
        return []
    q = ("SELECT f.claim, f.detail, f.status, c.name AS concept, c.chapter, "
         "p.source_id, p.page_no FROM fact f JOIN concept c ON c.id=f.concept_id "
         "LEFT JOIN span s ON s.id=f.span_id LEFT JOIN page p ON p.id=s.page_id "
         f"WHERE f.concept_id IN ({','.join('?' * len(concept_ids))}) "
         f"AND f.status != 'rejected' LIMIT {int(limit)}")
    return [dict(r) for r in store.db.execute(q, concept_ids)]


def context(store: Store, question: str) -> dict:
    """Everything the tutor should have in front of it for this question."""
    cs = resolve(store, question)
    ids = [c["id"] for c in cs]
    out = {"concepts": cs, "facts": facts(store, ids), "methods": [],
           "misconceptions": [], "connections": []}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    out["methods"] = [dict(r) for r in store.db.execute(
        "SELECT DISTINCT m.name, m.trigger, m.steps, m.common_error FROM method m "
        f"JOIN method_concept mc ON mc.method_id=m.id WHERE mc.concept_id IN ({marks})",
        ids)]
    out["misconceptions"] = [dict(r) for r in store.db.execute(
        f"SELECT wrong_belief, why_tempting FROM misconception WHERE concept_id IN ({marks})",
        ids)]
    out["connections"] = [dict(r) for r in store.db.execute(
        "SELECT c.concept_a, c.concept_b, c.relation, c.explanation, o.name AS other "
        "FROM connection c JOIN concept o ON o.id = c.concept_b "
        f"WHERE c.concept_a IN ({marks})", ids)]
    return out


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "why does ionisation enthalpy dip at oxygen?"
    st = Store()
    ctx = context(st, q)
    print(f"  Q: {q}\n")
    print("  concepts: " + (", ".join(c["name"] for c in ctx["concepts"]) or "—"))
    for f in ctx["facts"][:6]:
        cite = f"{f['source_id']} p{f['page_no']}" if f.get("source_id") else "uncited"
        print(f"    · {f['claim']}   [{cite}]")
    for m in ctx["methods"][:2]:
        print(f"  method: {m['name']} — {m['trigger']}")
    for m in ctx["misconceptions"][:2]:
        print(f"  trap:   {m['wrong_belief']}")
    for c in ctx["connections"][:3]:
        print(f"  links:  {c['other']} ({c['relation']})")
    st.close()
