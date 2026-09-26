#!/usr/bin/env python3
"""Comparative voice ranking — the per-clip rubric saturated at 5/5 for all ten.

Rating clips one at a time gave every candidate full marks, which ranks nothing. Judged
side by side in a single request the model is forced to discriminate, and a forced
ranking with distinct positions is far more informative than ten independent ceilings.
Run twice with the order reversed to check the ranking is about the voices and not about
their position in the prompt.
"""
from __future__ import annotations

import json
from pathlib import Path

from google.genai import types

from gclient import JUDGE, client, parse_json, retry

OUT = Path(__file__).resolve().parent.parent / "build" / "audition"

PROMPT = """You are casting the narrator for a Hinglish JEE physics micro-lecture for
students across India. Below are {n} candidate takes of the SAME line, labelled by voice.

Listen to all of them and rank them against each other. You MUST discriminate — no ties,
no giving everyone top marks. Judge on, in priority order:
1. authentic Indian English accent (not American/British/neutral)
2. natural Hindi pronunciation in the Hinglish code-switching
3. warm mentor energy suited to a teenager who finds physics hard
4. crisp English technical terms (cathode, electron, physics)

Return ONLY JSON:
{{"ranking": ["BestVoice", "SecondVoice", ...all {n} in order...],
  "top3": [{{"voice":"...", "why":"one specific sentence about how it sounds"}}, ...],
  "worst": {{"voice":"...", "why":"..."}},
  "notes_on_accent": "are these genuinely Indian-accented, or generic English with Hindi words?"}}"""


def rank(order: list[str]) -> dict:
    c = client()
    parts = []
    for v in order:
        parts.append(f"--- candidate: {v} ---")
        parts.append(types.Part.from_bytes(data=(OUT / f"{v}.wav").read_bytes(),
                                           mime_type="audio/wav"))
    parts.append(PROMPT.format(n=len(order)))
    r = retry(lambda: c.models.generate_content(
        model=JUDGE, contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ), what="comparative rank")
    return parse_json(r.text)


def main():
    voices = [p.stem for p in sorted(OUT.glob("*.wav"))]
    print(f"ranking {len(voices)} voices: {voices}\n")

    fwd = rank(voices)
    rev = rank(list(reversed(voices)))

    print("forward order  :", fwd["ranking"])
    print("reversed order :", rev["ranking"])
    print("\ntop 3 (forward):")
    for t in fwd["top3"]:
        print(f"  {t['voice']:13s} {t['why']}")
    print(f"\nworst: {fwd['worst']['voice']} — {fwd['worst']['why']}")
    print(f"\naccent reality-check: {fwd['notes_on_accent']}")

    # Borda count across both orderings — a voice that only wins from one position is
    # being ranked by prompt order, not by sound.
    score = {v: 0 for v in voices}
    for r in (fwd, rev):
        for i, v in enumerate(r["ranking"]):
            if v in score:
                score[v] += len(voices) - i
    board = sorted(score.items(), key=lambda kv: -kv[1])
    print("\n=== combined (Borda, both orderings) ===")
    for v, s in board:
        print(f"  {v:13s} {s}")
    print(f"\nWINNER: {board[0][0]}")

    (OUT / "ranking.json").write_text(json.dumps(
        {"forward": fwd, "reversed": rev, "borda": board, "winner": board[0][0]},
        indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
