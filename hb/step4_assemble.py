#!/usr/bin/env python3
"""STEP 4 — Assemble the finished lesson.

Reuses the factory's step-9 assembler wholesale (intro/outro cards, per-beat fit, burned
captions, SRT, 9:16 reel cut) rather than reimplementing any of it. Two adaptations:

  * Narration was synthesised per hybrid SEGMENT, but the assembler works per BEAT, so a
    beat's segment wavs are concatenated back into one track here and their chunk
    manifests are re-offset onto the merged timeline. Captions are built from those
    chunks, so the offsets have to be exact or every caption after the first split beat
    drifts.
  * Clips come from ../manim (the 3b1b rebuild) instead of the factory's clips dir.

Outputs build/ML1.1_hybrid.mp4 and build/ML1.1_hybrid_reel.mp4.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import yaml

from gclient import MICRO, lesson_dirs, topic
from segments import beat_segments, segments

sys.path.insert(0, str(MICRO / "factory"))
from animate import render_manim  # noqa: E402
import assemble as _assemble  # noqa: E402
from assemble import assemble  # noqa: E402
from common import Ctx, ffprobe_duration  # noqa: E402
from scriptparse import parse_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
TOPIC = DIRS["topic"]
AUDIO = DIRS["audio"]
MANIM = DIRS["manim"]
BUILD = DIRS["build"]
CFG = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())
SCRIPT = DIRS["script"]


def merge_beat_audio(sids: list[str], out: Path) -> dict:
    """Concatenate a beat's segment wavs and re-offset their chunk manifests."""
    frames, params, chunks, offset, spoken = bytearray(), None, [], 0.0, []
    for sid in sids:
        meta = json.loads((AUDIO / f"{sid}.chunks.json").read_text())
        with wave.open(str(AUDIO / f"{sid}.wav"), "rb") as w:
            if params is None:
                params = w.getparams()
            frames += w.readframes(w.getnframes())
        for c in meta["chunks"]:
            chunks.append({**c, "start": round(c["start"] + offset, 3),
                           "end": round(c["end"] + offset, 3)})
        spoken.append(meta["spoken_text"])
        offset += meta["duration"]

    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))
    return {"wav_path": str(out), "duration": round(offset, 3),
            "chunks": chunks, "spoken_text": " ".join(spoken)}


def rounded_mask(w: int, h: int, radius: int, path: Path) -> Path:
    """Alpha mask for the avatar inset. This ffmpeg has no drawtext/freetype, and
    rounding via geq is unreadable, so the mask is drawn once with PIL."""
    from PIL import Image, ImageDraw
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def composite_avatar(clip: Path, avatar: Path, out: Path, cfg: dict) -> Path:
    """Corner presence, per VISUAL_GRAMMAR.md §3.

    Composited onto the silent Manim clip BEFORE the factory assembler sees it, so the
    assembler, the captions and the reel cut all stay untouched. The inset sits above
    the caption band so it never collides with the burned subtitles.
    """
    v = cfg["video"]["full"]
    av = cfg.get("avatar", {})
    W, H = int(v["width"]), int(v["height"])
    aw = int(W * float(av.get("width_fraction", 0.20))) // 2 * 2
    ah = int(aw * 9 / 16) // 2 * 2
    # True bottom-right corner of the FINISHED frame. This runs after the assembler has
    # inset the picture into its caption-band layout, so the corner is mostly reserved
    # margin; burned captions render centred and do not reach this far right.
    x = W - aw - int(av.get("margin_x", 18))
    y = H - ah - int(av.get("margin_y", 10))

    mask = rounded_mask(aw, ah, radius=int(ah * 0.10),
                        path=out.parent / "_avatar_mask.png")
    fc = (f"[1:v]scale={aw}:{ah},format=rgba[av];"
          f"[2:v]format=gray,scale={aw}:{ah}[m];"
          f"[av][m]alphamerge[avm];"
          f"[0:v][avm]overlay={x}:{y}:format=auto[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-i", str(avatar),
                    "-i", str(mask), "-filter_complex", fc, "-map", "[v]",
                    "-map", "0:a?", "-c:a", "copy",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def main():
    beats = parse_script(SCRIPT)
    segs_by_beat = beat_segments(segments())

    audio_metas, clips = [], []
    merged_dir = BUILD / "beat_audio"
    for b in beats:
        sids = [s.sid for s in segs_by_beat[b.index]]
        meta = merge_beat_audio(sids, merged_dir / f"{b.slug}.wav")
        meta.update({"slug": b.slug, "beat": b.index, "segments": sids})
        audio_metas.append(meta)

        clip = MANIM / f"{b.slug}.mp4"
        if not clip.exists():
            print(f"  [4] missing clip {clip} — run step2_manim.py first")
            return 1

        clips.append({"slug": b.slug, "beat": b.index, "path": str(clip),
                      "duration": ffprobe_duration(clip)})
        print(f"  [4] {b.slug}: audio {meta['duration']:6.1f}s from {len(sids)} segment(s), "
              f"clip {clips[-1]['duration']:6.1f}s")

    # The assembler insets each segment into a caption-band frame and pads with the
    # brand background. The rebuild is pure black, so the pad colour has to be black
    # too or every beat gets a faint lighter border around the picture.
    CFG["brand"]["colors"]["bg"] = "#000000"

    ctx = Ctx(topic_id=TOPIC,
              topic={"title": "Discovery of the Electron"},
              cfg=CFG, lesson_dir=ROOT)
    ctx.ensure_dirs()

    # ML1.1's reel patterns matched nothing in any other lesson, and the fallback
    # quietly took each beat's first 12 seconds — which is why the reel ended
    # mid-sentence. Windows are per topic now.
    topics = yaml.safe_load((MICRO / "factory" / "topics.yaml").read_text())
    picks = next((r.get("reel_picks") for r in topics["topics"]
                  if r.get("id") == TOPIC), None)
    if picks:
        _assemble.REEL_PICKS = [tuple(p) for p in picks]
        print(f"  [4] reel windows: {[p[0] for p in picks]}")

    print("  [4] rendering brand cards…")
    cards = {}
    br = CFG["brand"]
    # Per-topic brand overrides, so a Gujarati lesson does not sign off in Hinglish.
    trow = next((r for r in topics["topics"] if r.get("id") == TOPIC), {})
    card_env = {"SAARTHI_TITLE": trow.get("title", ctx.topic.get("title", "")),
                "SAARTHI_HANDLE": br["handle"],
                "SAARTHI_TAGLINE": trow.get("brand_tagline", br["tagline"]),
                "SAARTHI_CTA": trow.get("brand_cta", br["outro_cta"])}
    if trow.get("brand_next"):
        card_env["SAARTHI_NEXT"] = trow["brand_next"]
    for name, secs in (("Intro", br["intro_seconds"]), ("Outro", br["outro_seconds"])):
        dest = ctx.build_dir / f"{name.lower()}.mp4"
        ok, err = render_manim(DIRS["scenes"] / "brand.py", name, dest, CFG,
                               name.lower(), float(secs), extra_env=card_env)
        if not ok:
            raise RuntimeError(f"{name} card failed: {err[-400:]}")
        cards[name.lower()] = str(dest)

    # Avatar goes on AFTER each beat is fitted and inset, so it lands in the reserved
    # corner instead of being scaled into the picture. The reel is cut from these same
    # segments, so both outputs get the corrected placement from one change.
    av_cfg = CFG.get("avatar", {})

    def overlay_avatar(b, seg):
        av_clip = BUILD / "avatar" / f"{b.slug}.mp4"
        if not (av_cfg.get("enabled") and av_clip.exists()):
            return
        tmp = seg.with_name(seg.stem + "_av.mp4")
        composite_avatar(seg, av_clip, tmp, CFG)
        tmp.replace(seg)
        print(f"  [4] {b.slug}: avatar composited into the corner")

    print("  [4] assembling…")
    result = assemble(ctx, beats, clips, audio_metas, cards,
                      post_segment=overlay_avatar)

    BUILD.mkdir(parents=True, exist_ok=True)
    for src, dst in ((ctx.final_mp4, BUILD / f"{TOPIC}.mp4"),
                     (ctx.reel_mp4, BUILD / f"{TOPIC}_reel.mp4")):
        if Path(src).exists():
            shutil.move(str(src), dst)
            print(f"  [4] -> {dst.relative_to(ROOT)}  {ffprobe_duration(dst):.1f}s")

    (BUILD / "assemble_report.json").write_text(
        json.dumps({"total_seconds": result["total_seconds"],
                    "srt": result["srt"], "reel": result.get("reel", {})},
                   indent=2, ensure_ascii=False))
    print(f"\n  total {result['total_seconds']:.1f}s "
          f"({result['total_seconds'] / 60:.2f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
