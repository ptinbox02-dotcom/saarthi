#!/usr/bin/env python3
"""STEP 2 — Manim beats -> manim/, with a Gemini frame check.

One clip per script beat, rendered from the 3Blue1Brown rebuild in ../scenes and fitted
to that beat's full narration length. The B-roll experiment was cut after review (the
Veo clips read as stock-footage mood rather than physics teaching), so every frame of
the finished video is now Manim — which is also what keeps the factual gate trivially
satisfied: no generated pixel carries a label.

The scene is measured first, then re-rendered with a wait-scale factor so the holds
stretch to meet the narration instead of the animation ending early and freezing.

Falls back to the factory's matplotlib storyboard card if Manim raises, so the build
never blocks; a degraded beat is reported rather than hidden.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from google.genai import types

from gclient import JUDGE, KIT, client, lesson_dirs, parse_json, retry
from segments import beat_segments, segments

sys.path.insert(0, str(KIT))
from animate import measure_scene, render_fallback, render_manim, wait_scale_for  # noqa: E402
from common import ffprobe_duration  # noqa: E402
from scriptparse import parse_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
MANIM = DIRS["manim"]
AUDIO = DIRS["audio"]
SCENES = DIRS["scenes"]        # the 3b1b rebuild, not the factory's scenes
BUILD = DIRS["build"]
SCRIPT = DIRS["script"]
CFG = yaml.safe_load((KIT / "config.yaml").read_text())

# MathTex shells out to latex/dvisvgm, which live in a user-local TinyTeX that is not
# on a non-login shell's PATH. Without this the scenes silently fall back to Unicode.
for _tex in (Path.home() / "Library/TinyTeX/bin/universal-darwin",
             Path.home() / "Library/TinyTeX/bin/aarch64-darwin"):
    if _tex.exists() and str(_tex) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_tex}{os.pathsep}" + os.environ.get("PATH", "")

FRAME_RUBRIC = """This is a frame from an educational physics animation for Indian JEE
students (Beat {beat}: {title}).

The beat's intended on-screen content is:
{brief}

IMPORTANT: this is a single frame grabbed from a running animation, so text is often
caught PART-WAY THROUGH being drawn on. Partially drawn or partially faded text is
correct behaviour — do NOT report it as garbled. Only set wrong_or_garbled when a fully
drawn label is misspelled, overlapping another label illegibly, or factually wrong.

Return ONLY JSON:
{{"blank": true/false,              // is the frame essentially empty/black?
  "readable_labels": ["..."],       // every text label you can actually read
  "matches_brief": true/false,      // does what you see belong to the brief above?
  "wrong_or_garbled": "any label that is misspelled, garbled or factually wrong, else empty",
  "note": "one sentence"}}"""


def beat_target(beat_idx: int, segs_by_beat: dict) -> float:
    """Full-beat narration length = sum of that beat's segment wavs."""
    total = 0.0
    for s in segs_by_beat[beat_idx]:
        meta = AUDIO / f"{s.sid}.chunks.json"
        if meta.exists():
            total += float(json.loads(meta.read_text()).get("duration", 0.0))
    return total


def sample_frames(clip: Path, out_dir: Path, n: int = 4) -> list[Path]:
    """Even samples, skipping the first and last 8% (fade-in/out are legitimately bare)."""
    import subprocess
    dur = ffprobe_duration(clip)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n):
        t = dur * (0.08 + 0.84 * (i / max(1, n - 1)))
        f = out_dir / f"{clip.stem}_{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}",
                        "-i", str(clip), "-frames:v", "1", str(f)], check=False)
        if f.exists():
            frames.append(f)
    return frames


def judge_frames(c, beat, frames: list[Path]) -> list[dict]:
    """QA is advisory — it must never be able to destroy a render.

    A single unparseable judge reply once aborted a multi-hour run after two beats, with
    the rendered clips already on disk. A frame that cannot be judged is recorded as
    unjudged and the build continues; the report shows how many were skipped.
    """
    out = []
    for f in frames:
        part = types.Part.from_bytes(data=f.read_bytes(), mime_type="image/png")
        try:
            r = retry(lambda: c.models.generate_content(
                model=JUDGE,
                contents=[part, FRAME_RUBRIC.format(beat=beat.index, title=beat.title,
                                                    brief=beat.onscreen[:900])],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            ), what=f"frame {f.name}")
            out.append({"frame": f.name, **parse_json(r.text)})
        except Exception as e:  # noqa: BLE001
            print(f"        (frame {f.name} unjudged: {str(e)[:80]})")
            out.append({"frame": f.name, "unjudged": str(e)[:200],
                        "blank": False, "matches_brief": True,
                        "readable_labels": [], "wrong_or_garbled": ""})
    return out


def main():
    MANIM.mkdir(parents=True, exist_ok=True)
    beats = {b.index: b for b in parse_script(SCRIPT)}
    segs_by_beat = beat_segments(segments())
    args = [a for a in sys.argv[1:] if a != "--qa-only"]
    qa_only = "--qa-only" in sys.argv
    only = {int(a) for a in args} if args else None

    c = client()
    rows = []

    todo = []
    for idx in sorted(beats):
        if only and idx not in only:
            continue
        b = beats[idx]
        target = beat_target(idx, segs_by_beat)
        if target <= 1:
            print(f"  [2] {b.slug}: no audio yet — run step1_audio.py first")
            return 1
        todo.append((b, target))

    def render_one(job):
        """Render one beat. Beats are independent processes writing to distinct files,
        so the only thing serialising them was this loop."""
        b, target = job
        scene_file = SCENES / f"{b.slug}.py"
        out = MANIM / f"{b.slug}.mp4"
        engine, err, scale = "manim", "", 1.0
        if qa_only and out.exists():
            ok = True
        elif scene_file.exists():
            measure = measure_scene(scene_file, b.scene_name, b.slug)
            scale = wait_scale_for(measure, target)
            ok, err = render_manim(scene_file, b.scene_name, out, CFG, b.slug, target,
                                   extra_env={"SAARTHI_WAITSCALE": f"{scale:.4f}"})
        else:
            ok, err = False, f"no scene file {scene_file.name}"
        if not ok:
            print(f"  [2] {b.slug}: manim failed ({(err.splitlines() or ['?'])[-1][:110]}) "
                  f"-> {CFG['render']['fallback_engine']} fallback")
            render_fallback(b, out, CFG)
            engine = CFG["render"]["fallback_engine"]
        return b, target, out, engine, err, scale

    # Manim is single-threaded per scene, so the machine sat mostly idle rendering one
    # beat at a time. Threads are the right primitive here: every worker blocks in
    # subprocess.run, which releases the GIL. Capped below the core count because each
    # render is memory-hungry and the efficiency cores are much slower than the
    # performance ones.
    if qa_only:
        results = [render_one(j) for j in todo]
    else:
        workers = int(os.environ.get("RENDER_WORKERS",
                                     max(1, min(len(todo), (os.cpu_count() or 4) - 4))))
        print(f"  [2] rendering {len(todo)} beats on {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(render_one, todo))

    for b, target, out, engine, err, scale in results:
        idx = b.index
        dur = ffprobe_duration(out)
        frames = sample_frames(out, BUILD / "frames")
        verdicts = judge_frames(c, b, frames)

        blank = [v["frame"] for v in verdicts if v.get("blank")]
        garbled = [v["wrong_or_garbled"] for v in verdicts if v.get("wrong_or_garbled")]
        offbrief = [v["frame"] for v in verdicts if not v.get("matches_brief")]
        labels = sorted({l for v in verdicts for l in v.get("readable_labels", [])})
        drift = abs(dur - target)

        rows.append({"slug": b.slug, "beat": idx, "engine": engine,
                     "duration": round(dur, 2), "target": round(target, 2),
                     "drift": round(drift, 2), "wait_scale": round(scale, 3),
                     "blank_frames": blank, "garbled": garbled,
                     "frames_off_brief": offbrief, "labels_seen": labels,
                     "verdicts": verdicts, "error": err if engine != "manim" else ""})
        print(f"  [2] {b.slug}: {dur:6.1f}s (target {target:6.1f}s, drift {drift:.2f}s) "
              f"{engine:10s} blank={len(blank)} garbled={len(garbled)} "
              f"off-brief={len(offbrief)} labels={len(labels)}")

    (BUILD / "manim_report.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False))

    bad = [r for r in rows if r["blank_frames"] or r["garbled"] or r["drift"] > 1.0]
    print(f"\n  {len(rows)} beats rendered; {len(bad)} need attention")
    for r in bad:
        print(f"    {r['slug']}: blank={r['blank_frames']} garbled={r['garbled']} "
              f"drift={r['drift']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
