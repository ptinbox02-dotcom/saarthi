#!/usr/bin/env python3
"""Build the chemistry knowledge layer.

    python3 knowledge/build.py ingest --sources <dir>      # no model needed
    python3 knowledge/build.py agents --chapter kech103     # needs a provider
    python3 knowledge/build.py status

Ingestion is deterministic and free: it reconstructs the notation that ordinary text
extraction destroys, and records which pages still need a vision pass. The agents are
the part that costs model calls, and they run at build time where latency is free and
a failed batch can simply be run again.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "extract"), str(HERE / "agents")]

import pdf                                                  # noqa: E402
import provider as provider_mod                             # noqa: E402
import questions as qparse                                  # noqa: E402
from roster import Concepts, Connections, Facts, Methods, Misconceptions  # noqa: E402
from store import Store                                     # noqa: E402
from verify import Verify                                   # noqa: E402


def ingest(store: Store, root: Path, only: str | None) -> None:
    pdfs = sorted(p for p in root.rglob("*.pdf") if not p.name.startswith("."))
    if only:
        pdfs = [p for p in pdfs if only in str(p)]
    print(f"  {len(pdfs)} pdf(s)")
    for path in pdfs:
        sid = re.sub(r"[^a-zA-Z0-9]+", "-", path.stem).strip("-").lower()
        kind = "sheet" if "sheet" in str(path).lower() else "textbook"
        store.add_source(sid, path.stem, kind, path)
        pages, fresh, spans_n = pdf.read(str(path)), 0, 0
        for p in pages:
            if store.page_unchanged(sid, p["page_no"], p["sha"]):
                continue
            pid = store.add_page(sid, p)
            spans = pdf.split_spans(p["text"])
            store.add_spans(pid, spans)
            fresh += 1; spans_n += len(spans)
        qs = qparse.parse(pages, sid)
        store.add_questions(sid, qs)
        store.db.commit()
        need = sum(1 for p in pages if p["residue"] > 3)
        print(f"  {path.stem[:38]:40} {len(pages):3d}p  {spans_n:4d} spans  "
              f"{len(qs):4d} questions  {need:3d} need vision")


AGENTS = {"concepts": Concepts, "facts": Facts, "methods": Methods,
          "connections": Connections, "misconceptions": Misconceptions,
          "verify": Verify}
ORDER = ["concepts", "facts", "verify", "methods", "connections", "misconceptions"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["ingest", "agents", "status"])
    ap.add_argument("--sources", default=str(HERE.parent / "sources"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--only", default=None, help="substring filter on source path")
    ap.add_argument("--chapter", default=None)
    ap.add_argument("--agent", default=None, choices=list(AGENTS))
    ap.add_argument("--provider", default=None)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    store = Store(a.db) if a.db else Store()
    try:
        if a.stage == "ingest":
            ingest(store, Path(a.sources), a.only)
        elif a.stage == "agents":
            prov = provider_mod.get(a.provider)
            print(f"  provider: {prov.name}")
            names = [a.agent] if a.agent else ORDER
            for n in names:
                cls = AGENTS[n]
                if n == "concepts":
                    src = a.only or a.chapter
                    if not src:
                        print("  concepts needs --only or --chapter"); continue
                    cls(store, prov, src, a.chapter or src).run(a.limit)
                else:
                    cls(store, prov).run(a.limit)
        counts = store.counts()
        print("\n  " + "  ".join(f"{k}={v}" for k, v in counts.items() if v))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
