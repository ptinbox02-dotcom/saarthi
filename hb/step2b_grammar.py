#!/usr/bin/env python3
"""Run the VISUAL_GRAMMAR eval over the rendered beats.

Separate from step 2 so it can be re-run without re-rendering: the check reads the
finished clips plus the emphasis events the scenes logged into their cues files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from gclient import MICRO, lesson_dirs

sys.path.insert(0, str(MICRO / "factory"))
from common import ffprobe_duration  # noqa: E402
from evals import eval_visual_grammar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
MANIM = DIRS["manim"]
BUILD = DIRS["build"]
CFG = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())


def main():
    clips = [{"slug": p.stem, "path": str(p), "duration": ffprobe_duration(p)}
             for p in sorted(MANIM.glob("beat*.mp4"))]
    if not clips:
        print("no clips in manim/ — run step2_manim.py first")
        return 1

    res = eval_visual_grammar(clips, CFG, MANIM)
    print(f"  visual grammar: {'PASS' if res.passed else 'FAIL'} "
          f"(score {res.score})")
    print(f"  {'slug':10s} {'dur':>6s} {'moves':>6s} {'need':>5s} {'static':>7s}")
    for b in res.details["beats"]:
        flag = ""
        if b["longest_static_seconds"] > CFG["direction"]["max_static_seconds"]:
            flag += "  <- static too long"
        if b["emphasis"] < b["emphasis_needed"]:
            flag += "  <- too few moves"
        print(f"  {b['slug']:10s} {b['duration']:6.1f} {b['emphasis']:6d} "
              f"{b['emphasis_needed']:5d} {b['longest_static_seconds']:6.1f}s{flag}")
    if not res.passed:
        print("\n  problems:")
        for p in res.feedback.split("; "):
            print(f"    - {p}")
    (BUILD / "visual_grammar.json").write_text(
        json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
