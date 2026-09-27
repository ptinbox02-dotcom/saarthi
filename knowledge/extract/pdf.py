"""Get text out of a PDF without destroying the chemistry.

The obvious approach — pypdf's extract_text — silently ruins the notation that carries
the meaning. Measured on these sources: zero unicode sub- or superscripts across 143
sampled pages, with [Ag(CN)₂]⁻ arriving as "2Ag CN" and E°(Zn²⁺,Zn) as three fragments
on separate lines. A model reading that cannot tell it is wrong.

The information was never missing. A PDF records each run's font size and baseline, and
a run set about a third smaller than its line's body text is a sub- or superscript
depending on which side of the baseline it sits. Rebuilding from that recovered 372
sub/superscripts across 72 NCERT pages and 836 across 71 sheet pages, deterministically,
with no model and no network.

What it does not fix: stacked constructs, where the script is its own line object rather
than a run inside one — a complex ion's bracket-and-charge, or E° carrying both a
subscript and a degree sign. Those pages flag themselves via `residue` and are the only
ones that need a vision pass.
"""
from __future__ import annotations

import hashlib
import re

import pymupdf

# U+2212 and the en dash both appear as minus in these PDFs; ASCII hyphen does not
SUB = str.maketrans("0123456789+-=()n\u2212\u2013", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₙ₋₋")
SUP = str.maketrans("0123456789+-=()n\u2212\u2013", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ⁻⁻")
PUA = re.compile(r"[-]")          # private-use glyphs: arrows, big braces
SIMPLE = set("0123456789+-=()n\u2212\u2013")
# What stacked notation leaves behind: a line holding nothing but a script fragment.
# A bare page number looks identical, so a lone plain integer does not count — it
# would have flagged every page in the book for a vision pass it does not need.
ORPHAN = re.compile(r"(?m)^\s*(?![0-9]{1,3}\s*$)[\d+\-–—°⁺⁻₊₋\u2212]{1,3}\s*$")
ARROWS = {"": "", "": "", "": ""}


def _script(text: str, kind: str) -> str:
    """Unicode where it exists, LaTeX where it does not."""
    body = text.strip()
    table = SUB if kind == "sub" else SUP
    if body and all(c in SIMPLE for c in body):
        return body.translate(table)
    return ("_" if kind == "sub" else "^") + "{" + body + "}"


def page_text(page, ratio: float = 0.86, lift: float = 0.6) -> str:
    """Reconstruct one page, span by span, using size and baseline."""
    out: list[str] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            body = max(s["size"] for s in spans)
            full = [s for s in spans if s["size"] >= body * ratio]
            base = max(s["origin"][1] for s in full) if full else spans[0]["origin"][1]
            buf: list[str] = []
            for s in spans:
                t = PUA.sub("", s["text"])
                if not t.strip():
                    continue
                dy = s["origin"][1] - base
                small = s["size"] < body * ratio
                if small and dy > lift:
                    buf.append(_script(t, "sub"))
                elif small and dy < -lift:
                    buf.append(_script(t, "sup"))
                else:
                    buf.append(t)
            # a superscript split across two runs must not keep the gap: Zn² ⁺ -> Zn²⁺
            joined = re.sub(r"(?<=[⁰-⁹⁺⁻])\s+(?=[⁰-⁹⁺⁻])", "", "".join(buf))
            joined = re.sub(r"(?<=[₀-₉₊₋])\s+(?=[₀-₉₊₋])", "", joined)
            out.append(joined)
    return "\n".join(out)


def read(path: str) -> list[dict]:
    """Every page of a PDF, reconstructed, with the residue that needs a vision pass."""
    doc = pymupdf.open(path)
    pages = []
    for n, page in enumerate(doc):
        text = page_text(page)
        pages.append({
            "page_no": n,
            "text": text,
            "sha": hashlib.sha1(text.encode()).hexdigest()[:16],
            "chars": len(text),
            "residue": len(ORPHAN.findall(text)),
        })
    doc.close()
    return pages


SECTION = re.compile(r"^\s*\d+\.\d+(\.\d+)?\s+\S")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
TARGET, HARD = 700, 1100


def _cut(body: str) -> list[str]:
    """Break a long passage on sentence boundaries, near TARGET characters.

    A citation is only useful if a human can check it in seconds. Whole-page spans made
    verification vacuous — "does this 4,400-character span support the claim" is yes for
    anything in the chapter — so passages are cut to something a reader can scan, and
    cut at sentence ends so a formula or a clause is never severed mid-thought.
    """
    if len(body) <= HARD:
        return [body]
    out, buf = [], ""
    for sent in SENT.split(body):
        if buf and len(buf) + len(sent) > TARGET:
            out.append(buf.strip()); buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf.strip():
        out.append(buf.strip())
    return out


def split_spans(text: str) -> list[dict]:
    """Break a page into passages worth citing.

    A paragraph, a numbered section, a question. Fine enough that a citation points at
    something checkable; coarse enough that it still carries its own meaning.
    """
    spans, buf = [], []

    def flush():
        if not buf:
            return
        body = " ".join(x.strip() for x in buf if x.strip())
        buf.clear()
        if len(body) <= 25:
            return
        for piece in _cut(body):
            if len(piece) > 25:
                spans.append({"kind": kind_of(piece), "text": piece})

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif SECTION.match(line) and buf:
            flush(); buf.append(line)
        elif re.match(r"^\s*\d{1,3}\.\s", line) and buf:
            flush(); buf.append(line)
        else:
            buf.append(line)
    flush()
    return spans


def kind_of(body: str) -> str:
    if re.match(r"^\s*\d{1,3}\.\s", body):
        return "question"
    if re.search(r"[→⇌=]", body) and len(body) < 220 and re.search(r"[A-Z][a-z]?[₀-₉]", body):
        return "formula"
    if len(body) < 90 and body.isupper():
        return "heading"
    return "prose"
