#!/usr/bin/env python3
"""Load a chapter's knowledge into the corpus, refusing anything that cannot be cited.

    python3 knowledge/seed/load.py knowledge/seed/kech103.json

Every fact, method and misconception names the span it came from, and the span must
exist and belong to the chapter it claims. A citation that does not resolve is not a
formatting problem — it means the claim was written from memory rather than from the
source, which is exactly the failure the corpus exists to prevent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from store import Store   # noqa: E402


def load(path: Path, db: str | None = None) -> int:
    data = json.loads(path.read_text())
    store = Store(db) if db else Store()
    src = data["source"]

    valid = {r["id"] for r in store.db.execute(
        "SELECT s.id FROM span s JOIN page p ON p.id = s.page_id WHERE p.source_id = ?",
        (src,))}
    if not valid:
        raise SystemExit(f"  no spans ingested for {src} — run build.py ingest first")

    bad = []
    for c in data["facts"]:
        if c[3] not in valid:
            bad.append(("fact", c[0], c[3]))
    for m in data["methods"]:
        if m.get("span") and m["span"] not in valid:
            bad.append(("method", m["name"], m["span"]))
    for m in data["misconceptions"]:
        if m[3] not in valid:
            bad.append(("misconception", m[0], m[3]))
    if bad:
        for kind, who, sid in bad[:12]:
            print(f"  !! {kind} '{str(who)[:40]}' cites span {sid}, which is not in {src}")
        raise SystemExit(f"  refusing to load: {len(bad)} unresolvable citation(s)")

    for c in data["concepts"]:
        store.upsert_concept(c["id"], name=c["name"], summary=c["summary"],
                             chapter=data["chapter"], syllabus_seq=c["seq"],
                             status="candidate")
    ids = {c["id"] for c in data["concepts"]}

    store.insert("fact", [{"concept_id": f[0], "claim": f[1], "detail": f[2],
                           "span_id": f[3], "status": "candidate"}
                          for f in data["facts"] if f[0] in ids])

    for m in data["methods"]:
        cur = store.db.execute(
            "INSERT INTO method(name,trigger,steps,common_error,difficulty,span_id,status)"
            " VALUES (?,?,?,?,?,?,'candidate')",
            (m["name"], m["trigger"], json.dumps(m["steps"], ensure_ascii=False),
             m.get("common_error", ""), m.get("difficulty", ""), m.get("span")))
        for cid in m.get("concepts", []):
            if cid in ids:
                store.db.execute(
                    "INSERT OR IGNORE INTO method_concept(method_id,concept_id) VALUES (?,?)",
                    (cur.lastrowid, cid))

    store.insert("connection", [{"concept_a": a, "concept_b": b, "relation": rel,
                                 "explanation": why, "status": "candidate"}
                                for a, b, rel, why in data["connections"]
                                if a in ids and b in ids])

    store.insert("misconception", [{"concept_id": m[0], "wrong_belief": m[1],
                                    "why_tempting": m[2], "status": "candidate"}
                                   for m in data["misconceptions"] if m[0] in ids])
    store.db.commit()
    counts = store.counts()
    store.close()
    print("  loaded  " + "  ".join(f"{k}={v}" for k, v in counts.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(load(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None))
