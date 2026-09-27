"""One agent, one job, one table.

Each agent reads a slice of the corpus, asks a model one well-shaped question about it,
and writes rows. They are separate rather than one large prompt for three reasons: a
batch that fails can be re-run alone, an agent that produces nonsense can be judged on
its own output, and the verifier has to be a different pass from the proposer or it is
only marking its own homework.
"""
from __future__ import annotations

import json
import traceback
from concurrent.futures import ThreadPoolExecutor


class Agent:
    name = "agent"
    table = ""
    schema: dict = {}
    batch_size = 1
    workers = 4

    def __init__(self, store, provider):
        self.store, self.provider = store, provider

    # --- to implement ---------------------------------------------------------
    def targets(self) -> list:
        """Units of work — a concept, a page, a chapter."""
        raise NotImplementedError

    def prompt(self, target) -> str:
        raise NotImplementedError

    def rows(self, target, result) -> list[dict]:
        """Turn the model's answer into rows for `self.table`."""
        raise NotImplementedError

    def images(self, target) -> list[bytes]:
        return []

    # --- the loop -------------------------------------------------------------
    def run(self, limit: int | None = None) -> int:
        targets = self.targets()
        if limit:
            targets = targets[:limit]
        if not targets:
            print(f"  {self.name:14} nothing to do")
            return 0
        run_id = self.store.begin_run(self.name, f"{len(targets)} targets",
                                      self.provider.name)
        produced, failed = 0, 0

        def one(t):
            try:
                result = self.provider.complete(self.prompt(t), self.schema,
                                                images=self.images(t))
                return self.rows(t, result)
            except Exception as e:                       # noqa: BLE001
                print(f"  {self.name}: {str(e)[:100]}")
                if os_debug():
                    traceback.print_exc()
                return None

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for rows in pool.map(one, targets):
                if rows is None:
                    failed += 1
                    continue
                produced += self.store.insert(self.table, rows)
        self.store.db.commit()
        self.store.end_run(run_id, produced, ok=failed == 0,
                           note=f"{failed} target(s) failed" if failed else None)
        print(f"  {self.name:14} {produced:5d} rows from {len(targets)} targets"
              + (f"  ({failed} failed)" if failed else ""))
        return produced


def os_debug() -> bool:
    import os
    return os.environ.get("SAARTHI_KB_DEBUG") == "1"


def listy(result, *keys):
    """Models disagree about the top-level key; take whichever list turned up."""
    if isinstance(result, list):
        return result
    for k in keys:
        v = (result or {}).get(k)
        if isinstance(v, list):
            return v
    return []
