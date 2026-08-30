#!/usr/bin/env python3
"""Final QA — Gemini watches the finished lesson end to end.

Uploaded via the Files API rather than inlined: a seven-minute 1080p master is far past
the inline request limit.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from google.genai import types

from gclient import JUDGE, client, parse_json, retry

ROOT = Path(__file__).resolve().parent.parent

RUBRIC = """You are the final QA reviewer for a Hinglish JEE physics micro-lecture
(ML 1.1 — Discovery of the Electron), rebuilt in a 3Blue1Brown visual style.

Watch the whole video, then return ONLY JSON:
{"reads_as_educational": true/false,   // does this read as a serious physics lesson
                                       // rather than stock footage / a social reel?
 "any_live_action_footage": true/false,// is ANY frame photographic/generated video
                                       // rather than vector animation?
 "labels_legible": true/false,
 "factual_errors": ["..."],            // any wrong physics or wrong on-screen value
 "captions_readable": true/false,
 "av_in_sync": true/false,
 "style_3b1b": n,                      // 1-5, how close to 3Blue1Brown's look
 "pacing": n,                          // 1-5
 "strongest_moment": "...",
 "weakest_moment": "...",
 "top_fixes": ["...", "..."]}

Known values that MUST be correct if shown: e/m of the electron 1.758 x 10^11 C/kg
(narration rounds to 1.76), proton e/m 9.578 x 10^7 C/kg, electron ~1836x lighter than a
hydrogen atom, Thomson 1897 measured e/m only (not e and not m separately)."""


def main():
    path = ROOT / "build" / (sys.argv[1] if len(sys.argv) > 1 else "ML1.1_hybrid.mp4")
    c = client()
    print(f"uploading {path.name} ({path.stat().st_size / 1048576:.1f} MB)…")
    f = c.files.upload(file=str(path))
    for _ in range(120):
        f = c.files.get(name=f.name)
        if f.state.name != "PROCESSING":
            break
        time.sleep(5)
    if f.state.name != "ACTIVE":
        print("upload failed:", f.state.name); return 1
    r = retry(lambda: c.models.generate_content(
        model=JUDGE, contents=[f, RUBRIC],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ), what="final QA")
    v = parse_json(r.text)
    print(json.dumps(v, indent=2, ensure_ascii=False))
    (ROOT / "build" / f"final_qa_{path.stem}.json").write_text(
        json.dumps(v, indent=2, ensure_ascii=False))
    try:
        c.files.delete(name=f.name)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
