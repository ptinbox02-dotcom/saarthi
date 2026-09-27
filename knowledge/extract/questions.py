"""Pull questions and answer keys out of a sheet, structurally.

No model needed. The sheets are regular: numbered stems, options as (1)-(4) or (A)-(D),
and an ANSWER KEY page at the end. That regularity is worth exploiting — every question
parsed here is one that does not have to be paid for twice, and the answer key is what
turns a wrong option into a catalogued misconception later.
"""
from __future__ import annotations

import re

NUM = re.compile(r"(?m)^\s*(\d{1,3})\.\s+(.*)$")
OPT = re.compile(r"\(\s*([1-4A-Da-d])\s*\)\s*([^()]{0,220})")
KEY_HEAD = re.compile(r"(?i)answer\s*key")
KEY_ROW = re.compile(r"(?m)^\s*(\d{1,3})\s*\.\s*\(?\s*([A-D1-4][,\s]*[A-D1-4]*)\s*\)?")


def _answer_key(pages) -> dict[int, str]:
    key: dict[int, str] = {}
    for p in pages:
        if not KEY_HEAD.search(p["text"]):
            continue
        for m in KEY_ROW.finditer(p["text"]):
            key[int(m.group(1))] = m.group(2).strip().replace(" ", "")
    return key


def parse(pages, source_id: str) -> list[dict]:
    key = _answer_key(pages)
    out: list[dict] = []
    for p in pages:
        if KEY_HEAD.search(p["text"]):
            continue                       # the key page is not a question page
        text = p["text"]
        marks = list(NUM.finditer(text))
        for i, m in enumerate(marks):
            body = text[m.start():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            n = int(m.group(1))
            opts = {o.group(1).upper(): o.group(2).strip()
                    for o in OPT.finditer(body) if o.group(2).strip()}
            stem = NUM.sub(lambda x: x.group(2), body.split("(1)")[0].split("(A)")[0], 1)
            stem = " ".join(stem.split())[:1200]
            if len(stem) < 15:
                continue
            out.append({"number": n, "stem": stem, "options": opts,
                        "answer": key.get(n), "page_id": f"{source_id}:{p['page_no']}"})
    return out
