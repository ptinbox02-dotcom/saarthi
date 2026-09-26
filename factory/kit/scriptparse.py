#!/usr/bin/env python3
"""Parse lessons/<id>/script.md into structured beats.

script.md doubles as the storyboard, so each beat carries:
  index, title, t_start, t_end, planned_seconds
  narration_md   – raw markdown narration (keeps ** and [Fx] tags)
  narration_tts  – cleaned text for TTS (bold stripped, [Fx] removed, pauses kept)
  onscreen       – the animation brief for step 7
  fact_tags      – ['F1', 'F2', ...] claimed in this beat
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from common import strip_md, FACT_TAG_RE

BEAT_HDR = re.compile(
    r"^###\s+Beat\s+(\d+)\s*[—\-–]\s*(.+?)\s*\((\d+):(\d\d)\s*[–\-—]\s*(\d+):(\d\d)\)\s*$"
)


@dataclass
class Beat:
    index: int
    title: str
    t_start: float
    t_end: float
    narration_md: str
    onscreen: str
    fact_tags: list = field(default_factory=list)

    @property
    def planned_seconds(self) -> float:
        return self.t_end - self.t_start

    @property
    def slug(self) -> str:
        return f"beat{self.index:02d}"

    @property
    def scene_name(self) -> str:
        return f"Beat{self.index:02d}"

    @property
    def narration_tts(self) -> str:
        t = strip_md(self.narration_md)
        t = FACT_TAG_RE.sub("", t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()

    @property
    def narration_plain(self) -> str:
        """Narration with pause markers removed too — used for word counts / captions."""
        t = self.narration_tts.replace("[PAUSE]", " ")
        return re.sub(r"\s+", " ", t).strip()


def parse_script(path: Path) -> list[Beat]:
    lines = path.read_text().splitlines()
    beats: list[Beat] = []
    i = 0
    while i < len(lines):
        m = BEAT_HDR.match(lines[i].strip())
        if not m:
            i += 1
            continue
        idx = int(m.group(1))
        title = m.group(2)
        t0 = int(m.group(3)) * 60 + int(m.group(4))
        t1 = int(m.group(5)) * 60 + int(m.group(6))
        # collect until the next beat header or a '---' that precedes a non-beat section
        body = []
        i += 1
        while i < len(lines) and not BEAT_HDR.match(lines[i].strip()):
            if lines[i].strip().startswith("## "):
                break
            body.append(lines[i])
            i += 1
        blob = "\n".join(body)

        nar = _section(blob, "NARRATION")
        ons = _section(blob, "ON-SCREEN")
        beats.append(Beat(
            index=idx, title=title, t_start=t0, t_end=t1,
            narration_md=nar, onscreen=ons,
            fact_tags=sorted(set(FACT_TAG_RE.findall(nar)), key=lambda s: int(s[2:-1])),
        ))
    beats.sort(key=lambda b: b.index)
    return beats


def _section(blob: str, name: str) -> str:
    """Pull the NARRATION blockquote or the ON-SCREEN paragraph out of a beat body."""
    # A heading may carry a parenthetical aside, e.g.
    #   **ON-SCREEN (direction: push-in zoom — see VISUAL_GRAMMAR.md):**
    # Both the heading being sought AND the lookahead that ends the previous section
    # have to tolerate it. When they did not, NARRATION swallowed the whole storyboard
    # brief (it ran on to the next `---`) and ON-SCREEN came back empty — silently, and
    # only for the one beat whose heading had been annotated.
    hdr = rf"\*\*{re.escape(name)}(?:\s*\([^)]*\))?:\*\*"
    nxt = r"\n\*\*[A-Z][A-Z \-]*(?:\s*\([^)]*\))?:\*\*"
    pat = re.compile(rf"{hdr}(.*?)(?={nxt}|\n---|\Z)", re.S)
    m = pat.search(blob)
    if not m:
        return ""
    txt = m.group(1)
    out = []
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith(">"):
            s = s[1:].strip()
        out.append(s)
    # collapse blockquote hard-wraps into paragraphs (blank line = paragraph break)
    paras, cur = [], []
    for s in out:
        if not s:
            if cur:
                paras.append(" ".join(cur)); cur = []
        else:
            cur.append(s)
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(p for p in paras if p).strip()


def reel_spec(path: Path) -> str:
    m = re.search(r"##\s*Reel teaser cut.*?\n(.*?)(?=\n##\s|\Z)", path.read_text(), re.S)
    return m.group(1).strip() if m else ""


def tts_notes(path: Path) -> str:
    m = re.search(r"##\s*Notes for step 8.*?\n(.*?)(?=\n##\s|\Z)", path.read_text(), re.S)
    return m.group(1).strip() if m else ""


if __name__ == "__main__":
    import sys
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "lessons/ML1.1/script.md")
    for b in parse_script(p):
        print(f"Beat {b.index} [{b.t_start:.0f}-{b.t_end:.0f}s = {b.planned_seconds:.0f}s] {b.title}")
        print(f"  facts: {b.fact_tags}")
        print(f"  words: {len(b.narration_plain.split())}")
        print(f"  onscreen: {b.onscreen[:90]}...")
