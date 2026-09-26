#!/usr/bin/env python3
"""script.md beats -> the segments the pipeline voices and renders.

One segment per beat. The split machinery below is retained but empty: beats were only
ever split so a Gemini B-roll cutaway could replace part of one, and the B-roll was cut
after review (build/COMPARISON.md). Keeping the mechanism costs nothing and keeping it
*used* cost a hard dependency on exact narration wording — the beat-5 marker broke the
moment the script was revised.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from gclient import KIT, lesson_dirs

sys.path.insert(0, str(KIT))
from scriptparse import parse_script  # noqa: E402

SCRIPT = lesson_dirs()["script"]


@dataclass
class Segment:
    sid: str            # b0, b1, b2, b2b, ...
    beat: int           # source beat index
    part: int           # 0 = whole beat or first half, 1 = second half
    narration: str      # the narration this segment speaks
    engine: str         # "manim" | "broll"
    broll: str = ""     # broll/<file>.mp4 when engine == broll
    overlay: str = ""   # text burned over a B-roll shot (never a fact-bearing label)

    @property
    def beat_slug(self) -> str:
        return f"beat{self.beat:02d}"


# Where a split beat divides. Each marker is the first words of the NEXT part and must
# fall on a sentence boundary, so no part ever starts mid-clause.
# Empty by design. Beats were split only so a B-roll cutaway could replace part of one;
# the B-roll was cut after review, so a split now buys nothing and costs a brittle
# coupling to exact narration wording — the beat-5 marker broke the moment the script
# was revised. One segment per beat.
SPLITS: dict[int, list[str]] = {}

# B-roll assignment, straight from shotlist.md + omni_prompts.md overlay lines.
# Every overlay is atmosphere or a date already established in Manim — never a value,
# formula or label the lesson depends on.
# Retained empty: the generated clips live in broll/ as experiment evidence, but no
# generated frame appears in the lesson (see build/COMPARISON.md).
BROLL: dict[str, tuple[str, str]] = {}

PART_SUFFIX = ["", "b", "c", "d"]


def _split_narration(text: str, markers: list[str]) -> list[str]:
    """Cut narration at each marker, in order. A missing marker is a hard error: it
    means script.md moved under us and the cut would silently land somewhere else."""
    parts, rest = [], text
    for m in markers:
        i = rest.find(m)
        if i < 0:
            raise RuntimeError(f"split marker {m!r} not found — script.md changed?")
        parts.append(rest[:i].strip())
        rest = rest[i:]
    parts.append(rest.strip())
    return [p for p in parts if p]


def segments() -> list[Segment]:
    beats = {b.index: b for b in parse_script(SCRIPT)}
    out: list[Segment] = []
    for i in sorted(beats):
        b = beats[i]
        texts = _split_narration(b.narration_tts, SPLITS.get(i, []))
        for part, text in enumerate(texts):
            sid = f"b{i}{PART_SUFFIX[part]}"
            br, ov = BROLL.get(sid, ("", ""))
            out.append(Segment(sid=sid, beat=i, part=part, narration=text,
                               engine="broll" if br else "manim",
                               broll=br, overlay=ov))
    return out


def beat_segments(segs: list[Segment]) -> dict[int, list[Segment]]:
    d: dict[int, list[Segment]] = {}
    for s in segs:
        d.setdefault(s.beat, []).append(s)
    for v in d.values():
        v.sort(key=lambda s: s.part)
    return d


if __name__ == "__main__":
    segs = segments()
    print(f"{len(segs)} segments\n")
    for s in segs:
        w = len(re.sub(r"\[PAUSE\]", " ", s.narration).split())
        tag = f"BROLL:{s.broll}" if s.engine == "broll" else "MANIM"
        print(f"  {s.sid:4s} beat{s.beat} part{s.part} {tag:34s} {w:3d}w  "
              f"{s.narration[:64]!r}")
