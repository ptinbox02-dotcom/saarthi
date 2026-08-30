#!/usr/bin/env python3
"""Export a lesson manifest — the contract between the factory and the classroom app.

The app needs more than an mp4. To pause a lesson without cutting a word in half, to
tell a live tutor exactly where the student is, and to keep live answers inside the same
verified scope the recorded lesson was gated against, it needs the timeline, the cue
points, the per-beat script and the fact sheet in one file.

Everything here already exists as a build artefact; this step only collects it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from gclient import MICRO, lesson_dirs, topic

sys.path.insert(0, str(MICRO / "factory"))
from common import ffprobe_duration  # noqa: E402
from evals import parse_fact_sheet  # noqa: E402
from scriptparse import parse_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "classroom" / "lessons"


def main():
    t = topic()
    d = lesson_dirs()
    build = d["build"]
    video = build / f"{t}.mp4"
    reel = build / f"{t}_reel.mp4"
    if not video.exists():                       # pre-rename layout
        alt = ROOT / "build" / f"{t}_hybrid.mp4"
        if alt.exists():
            video, reel = alt, ROOT / "build" / f"{t}_hybrid_reel.mp4"
    if not video.exists():
        print(f"  no video at {video}"); return 1

    beats = parse_script(d["script"])
    facts = parse_fact_sheet(d["facts"]) if d["facts"].exists() else {}

    # Derive the timeline from this lesson's own audio rather than reading the
    # assembler's timeline.json: that file is written to a shared build path and is
    # overwritten by whichever lesson assembled last, so every manifest was inheriting
    # another lesson's beat offsets. fit_segment makes each segment exactly as long as
    # its narration, so cumulative audio duration IS the timeline.
    import yaml
    cfg = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())
    cursor = float(cfg["brand"]["intro_seconds"])
    timeline, chunk_index = [], {}
    for b in beats:
        meta_p = d["audio"] / f"b{b.index}.chunks.json"
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text())
        timeline.append({"slug": b.slug, "start": cursor,
                         "video_seconds": meta["duration"]})
        chunk_index[b.slug] = (cursor, meta["chunks"])
        cursor += meta["duration"]

    # per-beat cue points, written by the scenes themselves
    cues = {}
    for c in sorted(d["manim"].glob("beat*.cues.json")):
        cues[c.stem.replace(".cues", "")] = json.loads(c.read_text()).get("cues", {})

    # Chunk boundaries are the whole point: they are the only safe places to pause.
    sys.path.insert(0, str(MICRO / "factory"))
    from assemble import caption_cues
    stops = []
    for slug, (offset, chunks) in chunk_index.items():
        for cue in caption_cues(chunks, offset):
            stops.append(round(cue["end"], 3))
    stops = sorted(set(stops))

    man = {
        "topic": t,
        "title": next((b.title for b in beats if b.index == 0), t),
        "video": f"/media/{t}/{video.name}",
        "video_path": str(video),
        "reel": f"/media/{t}/{reel.name}" if reel.exists() else None,
        "reel_path": str(reel) if reel.exists() else None,
        "duration": round(ffprobe_duration(video), 3),
        "language": "gu" if t.endswith("-gu") else "hi",
        # every safe pause point, so "raise hand" never cuts a word in half
        "safe_stops": stops,
        "beats": [{
            "index": b.index,
            "slug": b.slug,
            "title": b.title,
            "start": next((round(s["start"], 3) for s in timeline
                           if s.get("slug") == b.slug), None),
            "seconds": next((round(s["video_seconds"], 3) for s in timeline
                             if s.get("slug") == b.slug), None),
            "narration": b.narration_plain,
            "onscreen": b.onscreen,
            "facts": b.fact_tags,
            "cues": cues.get(b.slug, {}),
        } for b in beats],
        # the grounding set for live answers — the same gate the recording passed
        "facts": {k: {"claim": v["claim"], "detail": re.sub(r"\*\*", "", v["detail"])}
                  for k, v in (facts.get("facts") or {}).items()},
        "in_scope": facts.get("in_scope", []),
        "out_of_scope": facts.get("out_scope", []),
        "exact_numbers": facts.get("exact_numbers", []),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"{t}.json"
    p.write_text(json.dumps(man, indent=2, ensure_ascii=False))
    print(f"  {t}: {len(man['beats'])} beats, {len(man['facts'])} facts, "
          f"{len(stops)} safe stops, {man['duration']:.0f}s -> {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
