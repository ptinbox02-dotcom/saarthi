"""The corpus, as one SQLite file.

Not a service and not a graph database. At syllabus scale this is a few tens of
thousands of rows — it fits in memory, ships inside the container, and can be versioned,
copied and diffed like any other artifact. A network hop here would buy independent
deploys and cost a timeout path in the middle of a student's answer.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / "corpus" / "chemistry.db"


class Store:
    def __init__(self, path: Path | str = DEFAULT):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Agents fan out across threads; a connection is thread-bound unless told
        # otherwise, and the first run died on it. One connection plus one lock is the
        # right trade at this concurrency — a pool would buy nothing and cost a
        # write-ordering problem.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.executescript((ROOT / "schema.sql").read_text())

    def close(self):
        self.db.commit(); self.db.close()

    # ---- ingestion -----------------------------------------------------------
    def add_source(self, sid, title, kind, path, lang="en", cls=None):
        self.db.execute(
            "INSERT OR REPLACE INTO source(id,title,kind,lang,class,path) VALUES (?,?,?,?,?,?)",
            (sid, title, kind, lang, cls, str(path)))
        return sid

    def page_unchanged(self, sid, page_no, sha) -> bool:
        """Re-running a source must skip what has not moved, not duplicate it."""
        row = self.db.execute("SELECT sha FROM page WHERE id=?",
                              (f"{sid}:{page_no}",)).fetchone()
        return bool(row and row["sha"] == sha)

    def add_page(self, sid, p) -> str:
        pid = f"{sid}:{p['page_no']}"
        self.db.execute("DELETE FROM span WHERE page_id=?", (pid,))
        self.db.execute(
            "INSERT OR REPLACE INTO page(id,source_id,page_no,sha,chars,residue,needs_vision)"
            " VALUES (?,?,?,?,?,?,?)",
            (pid, sid, p["page_no"], p["sha"], p["chars"], p["residue"],
             1 if p["residue"] > 3 else 0))
        return pid

    def add_spans(self, pid, spans):
        self.db.executemany(
            "INSERT INTO span(page_id,ord,kind,text) VALUES (?,?,?,?)",
            [(pid, i, s["kind"], s["text"]) for i, s in enumerate(spans)])

    def add_questions(self, sid, rows):
        self.db.executemany(
            "INSERT INTO question(source_id,page_id,number,stem,options,answer) "
            "VALUES (?,?,?,?,?,?)",
            [(sid, r.get("page_id"), r.get("number"), r["stem"],
              json.dumps(r.get("options") or {}, ensure_ascii=False), r.get("answer"))
             for r in rows])

    # ---- what the agents read and write --------------------------------------
    def spans_for(self, source_id=None, kinds=("prose", "formula", "heading"), limit=None):
        q = ("SELECT s.id, s.kind, s.text, p.page_no, p.source_id FROM span s "
             "JOIN page p ON p.id = s.page_id WHERE s.kind IN (%s)"
             % ",".join("?" * len(kinds)))
        args = list(kinds)
        if source_id:
            q += " AND p.source_id = ?"; args.append(source_id)
        q += " ORDER BY p.source_id, p.page_no, s.ord"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(q, args)]

    def upsert_concept(self, cid, **kw):
        with self.lock:
            cols = ["id"] + list(kw)
            self.db.execute(
                f"INSERT OR REPLACE INTO concept({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", [cid] + list(kw.values()))
            return cid
    def insert(self, table, rows) -> int:
        with self.lock:
            if not rows:
                return 0
            cols = list(rows[0])
            self.db.executemany(
                f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [tuple(r[c] for c in cols) for r in rows])
            return len(rows)

        # ---- provenance of the build itself --------------------------------------
    def begin_run(self, agent, target, provider) -> int:
        cur = self.db.execute(
            "INSERT INTO agent_run(agent,target,provider,started_at) VALUES (?,?,?,?)",
            (agent, target, provider, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.db.commit()
        return cur.lastrowid

    def end_run(self, run_id, produced, ok=True, note=None):
        self.db.execute(
            "UPDATE agent_run SET finished_at=?, produced=?, ok=?, note=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), produced,
             1 if ok else 0, note, run_id))
        self.db.commit()

    def counts(self) -> dict:
        out = {}
        for t in ("source", "page", "span", "concept", "fact", "method", "derivation",
                  "connection", "question", "misconception", "figure"):
            out[t] = self.db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        return out
