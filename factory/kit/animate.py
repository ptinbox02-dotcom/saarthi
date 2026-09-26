#!/usr/bin/env python3
"""STEP 7 — Animate.

Renders one Manim Scene per beat from lessons/<id>/scenes/beatNN.py. Each scene is a
direct build of that beat's ON-SCREEN brief in script.md and drops a <slug>.cues.json
of named cue points, which step 9 uses to cut the reel.

Never blocks: if a scene file is missing, or Manim raises on it, the beat falls back to
a matplotlib+ffmpeg storyboard card built from the same ON-SCREEN text, and the failure
is recorded so qa_report.md shows which beats are degraded.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

from common import ROOT, VENV_PY, ffprobe_duration


def _manim_env(cue_dir: Path, slug: str, target: float, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.update({
        "SAARTHI_CUEDIR": str(cue_dir),
        "SAARTHI_SLUG": slug,
        "SAARTHI_TARGET": f"{target}",
        "PYTHONPATH": str(ROOT / "factory") + os.pathsep + env.get("PYTHONPATH", ""),
    })
    env.update(extra or {})
    return env


MEASURE_SNIPPET = r"""
import importlib, json, os, sys
sys.path.insert(0, os.environ["SAARTHI_SCENEDIR"])
sys.path.insert(0, os.environ["SAARTHI_FACTORY"])
from manim import tempconfig
mod = importlib.import_module(os.environ["SAARTHI_MODULE"])
cls = getattr(mod, os.environ["SAARTHI_CLASS"])
with tempconfig({"frame_rate": 2, "pixel_width": 160, "pixel_height": 90,
                 "dry_run": True, "write_to_movie": False, "disable_caching": True,
                 "verbosity": "ERROR", "progress_bar": "none"}):
    s = cls()
    s.render()
    print("SAARTHI_MEASURE " + json.dumps({"anim": s._anim_t, "wait": s._wait_t}))
"""


def measure_scene(scene_file: Path, scene_name: str, slug: str,
                  timeout: int = 300) -> dict | None:
    """Dry-run the scene (no frames) to learn its animation vs hold split."""
    env = _manim_env(Path("/tmp"), slug, 0.0, {
        "SAARTHI_SCENEDIR": str(scene_file.parent),
        "SAARTHI_FACTORY": str(ROOT / "factory"),
        "SAARTHI_MODULE": scene_file.stem,
        "SAARTHI_CLASS": scene_name,
        "SAARTHI_CUEDIR": "",
        "SAARTHI_WAITSCALE": "1.0",
    })
    try:
        p = subprocess.run([str(VENV_PY), "-c", MEASURE_SNIPPET],
                           cwd=scene_file.parent, capture_output=True, text=True,
                           env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    for line in p.stdout.splitlines():
        if line.startswith("SAARTHI_MEASURE "):
            return json.loads(line.split(" ", 1)[1])
    return None


def wait_scale_for(measure: dict | None, target: float,
                   lo: float = 1.0, hi: float = 10.0) -> float:
    if not measure or target <= 0 or measure["wait"] <= 0.01:
        return 1.0
    need = (target - measure["anim"]) / measure["wait"]
    return max(lo, min(hi, need))


def render_manim(scene_file: Path, scene_name: str, out_path: Path, cfg: dict,
                 slug: str, target: float, extra_env: dict | None = None,
                 timeout: int = 3600) -> tuple[bool, str]:
    v = cfg["video"]["full"]
    # Per-slug media dir: with beats rendering in parallel a shared media_dir races on
    # the Tex/text caches whenever two scenes typeset the same string. Isolation costs
    # a little duplicated LaTeX work and buys deterministic parallel renders.
    workdir = out_path.parent / "_manim" / slug
    workdir.mkdir(parents=True, exist_ok=True)
    # Caching is opt-in (SAARTHI_CACHE=1). Manim hashes each animation and reuses its
    # partial movie file, which makes a re-render of a mostly-unchanged beat far
    # cheaper — but it keys on the scene code, not on the audio, so it must not be on
    # by default for a pipeline that re-times clips to narration.
    cache_flags = [] if os.environ.get("SAARTHI_CACHE") == "1" else ["--disable_caching"]
    cmd = [str(VENV_PY), "-m", "manim", "render",
           "-r", f"{v['width']},{v['height']}", "--fps", str(v["fps"]),
           "--format", "mp4", *cache_flags,
           "--media_dir", str(workdir),
           "-o", slug,
           str(scene_file), scene_name]
    try:
        p = subprocess.run(cmd, cwd=scene_file.parent, capture_output=True, text=True,
                           env=_manim_env(out_path.parent, slug, target, extra_env),
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"manim render exceeded {timeout}s"
    hits = sorted(workdir.rglob(f"{slug}.mp4"), key=lambda f: f.stat().st_mtime)
    if p.returncode != 0 or not hits:
        tail = (p.stderr or p.stdout or "")[-900:]
        return False, tail
    shutil.copy2(hits[-1], out_path)
    return True, ""


# ----- fallback -------------------------------------------------------------
def render_fallback(beat, out_path: Path, cfg: dict) -> None:
    """matplotlib + ffmpeg storyboard card. Ugly but never blocks the pipeline."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    br = cfg["brand"]["colors"]
    v = cfg["video"]["full"]
    tmp = out_path.parent / f"_fallback_{beat.slug}"
    tmp.mkdir(parents=True, exist_ok=True)

    brief = re.sub(r"\s+", " ", beat.onscreen)
    lines = textwrap.wrap(brief, 58)[:9]
    dpi = 100
    fig = plt.figure(figsize=(v["width"] / dpi, v["height"] / dpi), dpi=dpi)
    fig.patch.set_facecolor(br["bg"])
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    ax.text(0.5, 0.88, f"Beat {beat.index} — {beat.title}", color=br["accent"],
            ha="center", va="center", fontsize=34, fontweight="bold")
    for i, l in enumerate(lines):
        ax.text(0.5, 0.70 - i * 0.075, l, color=br["ink"], ha="center", va="center", fontsize=22)
    ax.text(0.5, 0.06, cfg["brand"]["handle"], color=br["muted"],
            ha="center", va="center", fontsize=20)
    frame = tmp / "card.png"
    fig.savefig(frame, facecolor=br["bg"])
    plt.close(fig)

    subprocess.run(["ffmpeg", "-v", "error", "-loop", "1", "-i", str(frame),
                    "-t", f"{beat.planned_seconds}", "-r", str(v["fps"]),
                    "-vf", f"scale={v['width']}:{v['height']}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(out_path)],
                   check=True)
    (out_path.parent / f"{beat.slug}.cues.json").write_text(
        json.dumps({"duration": beat.planned_seconds, "cues": {}, "fallback": True}, indent=2))


# ----- the step --------------------------------------------------------------
def target_for(ctx, beat) -> tuple[float, str]:
    """Narration length wins when it exists; otherwise the beat's planned slot."""
    m = ctx.audio_dir / f"{beat.slug}.chunks.json"
    if m.exists():
        d = json.loads(m.read_text()).get("duration", 0)
        if d > 1:
            return float(d), "audio"
    return float(beat.planned_seconds), "planned"


def animate_beat(ctx, b, target: float) -> dict:
    cfg = ctx.cfg
    scene_file = ctx.scenes_dir / f"{b.slug}.py"
    out = ctx.clips_dir / f"{b.slug}.mp4"
    engine, err, scale = "manim", "", 1.0
    if scene_file.exists():
        measure = measure_scene(scene_file, b.scene_name, b.slug)
        scale = wait_scale_for(measure, target)
        ok, err = render_manim(scene_file, b.scene_name, out, cfg, b.slug, target,
                               extra_env={"SAARTHI_WAITSCALE": f"{scale:.4f}"})
    else:
        ok, err = False, f"no scene file at {scene_file.name}"
    if not ok:
        print(f"  [7] {b.slug}: manim failed ({(err.splitlines() or ['?'])[-1][:120]}) "
              f"→ {cfg['render']['fallback_engine']} fallback")
        render_fallback(b, out, cfg)
        engine = cfg["render"]["fallback_engine"]

    cues_path = ctx.clips_dir / f"{b.slug}.cues.json"
    cues = json.loads(cues_path.read_text()) if cues_path.exists() else {"cues": {}}
    return {"slug": b.slug, "beat": b.index, "path": str(out),
            "duration": ffprobe_duration(out), "engine": engine,
            "target": round(target, 3), "wait_scale": round(scale, 3),
            "cues": cues.get("cues", {}), "error": err if engine != "manim" else ""}


def animate(ctx, beats, feedback: str = "") -> list[dict]:
    ctx.ensure_dirs()
    clips = []
    for b in beats:
        target, src = target_for(ctx, b)
        print(f"  [7] {b.slug}: target {target:.1f}s ({src})")
        clips.append(animate_beat(ctx, b, target))
    (ctx.clips_dir / "clips.json").write_text(json.dumps(clips, indent=2))
    return clips


def render_brand_cards(ctx) -> dict:
    """2s intro + 3s outro from factory/brandscenes.py."""
    cfg = ctx.cfg
    br = cfg["brand"]
    out = {}
    env = {
        "SAARTHI_TITLE": ctx.topic.get("title", ""),
        "SAARTHI_HANDLE": br["handle"],
        "SAARTHI_TAGLINE": br["tagline"],
        "SAARTHI_CTA": br["outro_cta"],
    }
    for name, secs in (("Intro", br["intro_seconds"]), ("Outro", br["outro_seconds"])):
        dest = ctx.build_dir / f"{name.lower()}.mp4"
        ok, err = render_manim(ROOT / "factory" / "brandscenes.py", name, dest, cfg,
                               name.lower(), float(secs), extra_env=env)
        if not ok:
            _solid_card(dest, cfg, f"{br['handle']} · {br['tagline']}", float(secs))
        out[name.lower()] = str(dest)
    return out


def _solid_card(dest: Path, cfg: dict, text: str, secs: float):
    v, c = cfg["video"]["full"], cfg["brand"]["colors"]
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi",
        "-i", f"color=c={c['bg']}:s={v['width']}x{v['height']}:d={secs}:r={v['fps']}",
        "-vf", f"drawtext=text='{text}':fontcolor={c['accent']}:fontsize=64:"
               f"x=(w-text_w)/2:y=(h-text_h)/2",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(dest)], check=True)
