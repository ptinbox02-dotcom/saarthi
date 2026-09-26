#!/usr/bin/env python3
"""STEP 9 — Assemble.

Per beat: fit the animation to its narration, mux, and remember exactly where every
spoken chunk lands. Then concat intro + beats + outro, burn captions generated from
those chunk timings (so caption alignment is exact by construction, not estimated),
and auto-cut the 9:16 reel from the same fitted segments using the chunk manifest and
the cue points the Manim scenes emitted.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from common import ffprobe_duration

# how far the video may be time-scaled to meet its narration before we re-render
PTS_LO, PTS_HI = 0.80, 1.25
REFIT_BAND = 0.08          # |audio/video - 1| above this triggers a re-render

# Scenes use the full frame down to ~70px from the bottom, so burned captions would sit
# on top of their closing lines. Every segment is inset into a band-reserved frame
# instead. The padding colour is the scenes' own background, so the inset is invisible —
# the picture just sits slightly smaller with the caption in clean space beneath it.
CAPTION_BAND_PX = 150


def content_vf(cfg: dict) -> str:
    v, c = cfg["video"]["full"], cfg["brand"]["colors"]
    W, H = v["width"], v["height"]
    vh = H - CAPTION_BAND_PX
    vw = (round(vh * W / H) // 2) * 2
    return (f"scale={W}:{H},scale={vw}:{vh},"
            f"pad={W}:{H}:{(W - vw) // 2}:0:color={c['bg']},setsar=1")


def _run(cmd, timeout=1800):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{p.stderr[-1500:]}")
    return p


# ----- captions -------------------------------------------------------------
def _split_points(ws: list[str], n: int) -> list[int]:
    """Break a chunk into n cues, snapping each break to the nearest phrase boundary.

    Splitting on a fixed word count strands cues like "ka favourite trap hai: Thomson
    ne e". Preferring a word that ends in punctuation keeps each cue readable on its
    own, which matters because reels are watched muted.
    """
    ideal = [round(len(ws) * k / n) for k in range(1, n)]
    breaks = []
    for target in ideal:
        best, best_cost = target, 1e9
        for j in range(max(1, target - 2), min(len(ws) - 1, target + 2) + 1):
            if j in breaks:
                continue
            bonus = 0 if ws[j - 1].endswith((",", ";", ":", "—", "-", ".", "?", "!")) else 3
            cost = abs(j - target) + bonus
            if cost < best_cost:
                best, best_cost = j, cost
        breaks.append(best)
    return sorted(set(breaks))


def caption_cues(chunks: list[dict], offset: float, max_words=8, max_seconds=4.0) -> list[dict]:
    """One cue per spoken chunk, split so no cue is a wall of text."""
    cues = []
    for c in chunks:
        if c["kind"] != "speech" or not c["text"].strip():
            continue
        ws = c["text"].split()
        dur = c["end"] - c["start"]
        n = max(1, math.ceil(max(len(ws) / max_words, dur / max_seconds)))
        bounds = [0] + _split_points(ws, n) + [len(ws)] if n > 1 else [0, len(ws)]
        for a, b in zip(bounds, bounds[1:]):
            if b <= a:
                continue
            cues.append({
                "start": round(offset + c["start"] + dur * a / len(ws), 3),
                "end": round(offset + c["start"] + dur * b / len(ws), 3),
                "text": " ".join(ws[a:b]),
            })
    return cues


def _ts(t: float) -> str:
    h = int(t // 3600); m = int(t % 3600 // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict], path: Path):
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{c['text']}\n")
    path.write_text("\n".join(out), encoding="utf-8")


# ----- caption burn-in -------------------------------------------------------
# This machine's ffmpeg is built without libass *and* without freetype, so neither
# `subtitles` nor `drawtext` exists. Captions are therefore rendered with PIL into a
# single alpha video track and overlaid in one pass — which also removes an ffmpeg
# build-flag dependency and gives exact control over brand typography.
CAPTION_FONTS = ["/System/Library/Fonts/HelveticaNeue.ttc",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]


def _font(size: int):
    from PIL import ImageFont
    for p in CAPTION_FONTS:
        try:
            return ImageFont.truetype(p, size, index=1)     # index 1 = bold face
        except Exception:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    from PIL import ImageFont as IF
    return IF.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_STRIKE = 160          # the only large bitmap strike PIL will open in this face


def paste_emoji(img, ch: str, box_right: int, centre_y: int, size: int):
    """Colour emoji can't be drawn inline with a text face, so it is rendered on its
    own at the font's native strike and composited beside the line."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        f = ImageFont.truetype(EMOJI_FONT, EMOJI_STRIKE)
    except Exception:
        return 0
    tile = Image.new("RGBA", (EMOJI_STRIKE + 40, EMOJI_STRIKE + 40), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 10), ch, font=f, embedded_color=True)
    tile = tile.crop(tile.getbbox() or (0, 0, 1, 1)).resize((size, size), Image.LANCZOS)
    img.alpha_composite(tile, (box_right, centre_y - size // 2))
    return size


def caption_png(text: str, path: Path, w: int, h: int, cfg: dict,
                font_size: int, margin_bottom: int, emoji: str | None = None):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = _font(font_size)
    lines = _wrap(d, text, font, w * 0.84)
    lh = int(font_size * 1.32)
    box_h = lh * len(lines) + int(font_size * 0.7)
    box_w = int(max(d.textlength(l, font=font) for l in lines) + font_size * 1.1)
    if emoji:
        box_w += int(font_size * 1.35)
    x0, y0 = (w - box_w) // 2, h - margin_bottom - box_h
    d.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h],
                        radius=int(font_size * 0.35), fill=(6, 9, 13, 190))
    ink = cfg["brand"]["colors"]["ink"].lstrip("#")
    col = tuple(int(ink[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    y = y0 + int(font_size * 0.35)
    last_right, last_mid = 0, y
    for line in lines:
        lw = d.textlength(line, font=font)
        lx = (w - lw) / 2 - (int(font_size * 0.68) if emoji else 0)
        d.text((lx, y), line, font=font, fill=col)
        last_right, last_mid = int(lx + lw), y + lh // 2
        y += lh
    if emoji:
        paste_emoji(img, emoji, last_right + int(font_size * 0.3), last_mid,
                    int(font_size * 1.05))
    img.save(path)


def render_caption_track(cues: list[dict], total: float, w: int, h: int, fps: int,
                         workdir: Path, out: Path, cfg: dict,
                         font_size: int, margin_bottom: int,
                         emoji: str | None = None) -> Path:
    """One transparent video holding every caption, ready for a single overlay."""
    workdir.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    blank = workdir / "blank.png"
    Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(blank)

    entries, cursor = [], 0.0
    for i, c in enumerate(cues):
        if c["start"] > cursor + 1.0 / fps:
            entries.append((blank, c["start"] - cursor))
        p = workdir / f"cue_{i:04d}.png"
        caption_png(c["text"], p, w, h, cfg, font_size, margin_bottom, emoji)
        entries.append((p, max(1.0 / fps, c["end"] - max(c["start"], cursor))))
        cursor = max(cursor, c["end"])
    if total > cursor:
        entries.append((blank, total - cursor))

    lst = workdir / "captions.txt"
    body = "".join(f"file '{p.resolve()}'\nduration {d:.4f}\n" for p, d in entries)
    body += f"file '{entries[-1][0].resolve()}'\n"      # concat needs the last frame twice
    lst.write_text(body)
    _run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-fps_mode", "cfr", "-r", str(fps), "-c:v", "qtrle", "-pix_fmt", "argb",
          "-y", str(out)])
    return out


def burn_captions(base: Path, track: Path, out: Path, crf=20):
    _run(["ffmpeg", "-v", "error", "-i", str(base), "-i", str(track),
          "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
          "-map", "[v]", "-map", "0:a",
          "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
          "-pix_fmt", "yuv420p", "-c:a", "copy",
          "-movflags", "+faststart", "-y", str(out)])


# ----- per-beat fit ----------------------------------------------------------
def fit_segment(clip: Path, wav: Path, out: Path, cfg: dict) -> dict:
    """Make the picture exactly as long as the narration. Audio is never touched."""
    v = cfg["video"]["full"]
    vdur, adur = ffprobe_duration(clip), ffprobe_duration(wav)
    ratio = adur / vdur if vdur > 0 else 1.0
    pts = max(PTS_LO, min(PTS_HI, ratio))
    vf = (f"setpts={pts:.6f}*PTS,fps={v['fps']},{content_vf(cfg)},"
          f"tpad=stop_mode=clone:stop_duration=30")
    _run(["ffmpeg", "-v", "error", "-i", str(clip), "-i", str(wav),
          "-filter_complex", f"[0:v]{vf}[v]",
          "-map", "[v]", "-map", "1:a",
          "-t", f"{adur:.6f}",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart", "-y", str(out)])
    return {"pts": pts, "ratio": ratio, "video_in": vdur, "audio": adur,
            "out_seconds": ffprobe_duration(out)}


def silent_segment(clip: Path, out: Path, cfg: dict) -> float:
    v = cfg["video"]["full"]
    dur = ffprobe_duration(clip)
    _run(["ffmpeg", "-v", "error", "-i", str(clip),
          "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
          "-shortest", "-t", f"{dur:.6f}",
          "-vf", f"fps={v['fps']},{content_vf(cfg)}",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
          "-movflags", "+faststart", "-y", str(out)])
    return ffprobe_duration(out)


def concat(segments: list[Path], out: Path, workdir: Path):
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    _run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", "-movflags", "+faststart", "-y", str(out)])


# ----- the step ---------------------------------------------------------------
def assemble(ctx, beats, clips: list[dict], audio_metas: list[dict],
             brand_cards: dict, refit=None, post_segment=None) -> dict:
    """post_segment(beat, seg_path) runs after a beat has been fitted and inset into
    the caption-band frame, and may rewrite seg_path in place.

    That timing is the point: anything composited BEFORE fit_segment gets scaled and
    padded along with the content, so an overlay meant for the reserved margin ends up
    sitting on top of the picture. The reel is cut from these same segments, so a fix
    applied here lands in both outputs."""
    cfg = ctx.cfg
    ctx.ensure_dirs()
    bd = ctx.build_dir
    by_slug = {c["slug"]: c for c in clips}
    aud_by_slug = {m["slug"]: m for m in audio_metas}

    timeline, segments = [], []
    cursor = 0.0

    intro_seg = bd / "seg_intro.mp4"
    d = silent_segment(Path(brand_cards["intro"]), intro_seg, cfg)
    segments.append(intro_seg)
    timeline.append({"slug": "intro", "start": 0.0, "video_seconds": d,
                     "audio_seconds": d, "caption_cues": []})
    cursor += d

    for b in beats:
        clip = Path(by_slug[b.slug]["path"])
        meta = aud_by_slug[b.slug]
        wav = Path(meta["wav_path"])

        # the picture should already be close; if it is not, re-render this beat
        # at the narration's length rather than time-warping it into shape
        vdur, adur = ffprobe_duration(clip), ffprobe_duration(wav)
        if refit and vdur > 0 and abs(adur / vdur - 1.0) > REFIT_BAND:
            print(f"  [9] {b.slug}: clip {vdur:.1f}s vs narration {adur:.1f}s "
                  f"→ re-rendering the animation to fit")
            newclip = refit(b, adur)
            if newclip:
                clip = Path(newclip["path"])
                by_slug[b.slug] = newclip

        seg = bd / f"seg_{b.slug}.mp4"
        fit = fit_segment(clip, wav, seg, cfg)
        if post_segment:
            post_segment(b, seg)
        segments.append(seg)
        cues = caption_cues(meta["chunks"], cursor)
        timeline.append({"slug": b.slug, "beat": b.index, "start": round(cursor, 3),
                         "video_seconds": round(fit["out_seconds"], 3),
                         "audio_seconds": round(fit["audio"], 3),
                         "pts_applied": round(fit["pts"], 4),
                         "caption_cues": cues,
                         "chunks": meta["chunks"]})
        cursor += fit["out_seconds"]

    outro_seg = bd / "seg_outro.mp4"
    d = silent_segment(Path(brand_cards["outro"]), outro_seg, cfg)
    segments.append(outro_seg)
    timeline.append({"slug": "outro", "start": round(cursor, 3), "video_seconds": d,
                     "audio_seconds": d, "caption_cues": []})
    cursor += d

    # --- concat + burn captions ------------------------------------------------
    joined = bd / "joined.mp4"
    concat(segments, joined, bd)

    all_cues = [c for t in timeline for c in t["caption_cues"]]
    srt = bd / f"{ctx.topic_id}.srt"
    write_srt(all_cues, srt)

    v = cfg["video"]["full"]
    track = render_caption_track(all_cues, cursor, v["width"], v["height"], v["fps"],
                                 bd / "captrack", bd / "captions.mov", cfg,
                                 font_size=38, margin_bottom=54)
    burn_captions(joined, track, ctx.final_mp4)

    (bd / "timeline.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False))

    reel_info = {}
    if cfg["output"].get("make_reel_teaser", True):
        reel_info = build_reel(ctx, timeline)

    return {"timeline": timeline, "srt": str(srt), "reel": reel_info,
            "total_seconds": round(cursor, 3)}


# ----- reel -------------------------------------------------------------------
REEL_PICKS = [
    # (beat slug, regex that must match a spoken chunk, how many chunks to keep)
    ("beat00", None, None),                                   # the whole hook
    ("beat03", r"plus plate ki taraf|opposite charges|beam khud", 3),
    ("beat05", r"e ya m alag-alag|Sirf unka ratio|yeh galti", 3),
]
REEL_HOOK = "Tumhare ghar ki tubelight ek discovery hai"
REEL_HOOK_EMOJI = "💡"


def _pick_window(t: dict, pattern: str | None, keep: int | None) -> tuple[float, float]:
    speech = [c for c in t["chunks"] if c["kind"] == "speech"]
    if pattern is None:
        return 0.0, t["video_seconds"]
    rx = re.compile(pattern, re.I)
    hits = [i for i, c in enumerate(speech) if rx.search(c["text"])]
    if not hits:
        print(f"  [9] !! reel pattern {pattern!r} matched nothing in {t['slug']} — "
              f"falling back to its first 12s, which will cut mid-sentence. "
              f"Set reel_picks for this topic in topics.yaml.")
        return 0.0, min(12.0, t["video_seconds"])
    lo = hits[0]
    hi = min(len(speech) - 1, (lo + keep - 1) if keep else hits[-1])
    return speech[lo]["start"], speech[hi]["end"]


def build_reel(ctx, timeline) -> dict:
    cfg = ctx.cfg
    r = cfg["video"]["reel"]
    c = cfg["brand"]["colors"]
    bd = ctx.build_dir
    by_slug = {t["slug"]: t for t in timeline}

    parts, cues, cursor, picked = [], [], 0.0, []
    # Enumerated so that two windows from the SAME beat get distinct files. Keying the
    # output on the slug alone made the second window silently overwrite the first, so
    # the reel played one clip twice while the cue timings still assumed the original
    # pair — which pushed the captions past the end of the video.
    for pick_i, (slug, pattern, keep) in enumerate(REEL_PICKS):
        t = by_slug.get(slug)
        if not t:
            continue
        a, b = _pick_window(t, pattern, keep)
        if b - a < 0.5:
            continue
        src = bd / f"seg_{slug}.mp4"
        dst = bd / f"reel_{pick_i:02d}_{slug}.mp4"
        _run(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
              "-vf", f"scale={r['width']}:-2,pad={r['width']}:{r['height']}:0:"
                     f"(oh-ih)/2:color={c['bg']},fps={r['fps']},setsar=1",
              "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-y", str(dst)])
        seg_len = ffprobe_duration(dst)
        for cue in t["caption_cues"]:
            s = cue["start"] - t["start"]
            e = cue["end"] - t["start"]
            if e <= a or s >= b:
                continue
            cues.append({"start": round(cursor + max(0.0, s - a), 3),
                         "end": round(cursor + min(seg_len, e - a), 3),
                         "text": cue["text"]})
        parts.append(dst)
        picked.append({"slug": slug, "from": round(a, 2), "to": round(b, 2),
                       "seconds": round(seg_len, 2)})
        cursor += seg_len

    # CTA tail from the outro card
    cta = bd / "reel_outro.mp4"
    _run(["ffmpeg", "-v", "error", "-i", str(bd / "seg_outro.mp4"),
          "-vf", f"scale={r['width']}:-2,pad={r['width']}:{r['height']}:0:"
                 f"(oh-ih)/2:color={c['bg']},fps={r['fps']},setsar=1",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-y", str(cta)])
    parts.append(cta)
    cursor += ffprobe_duration(cta)
    picked.append({"slug": "outro", "seconds": round(ffprobe_duration(cta), 2)})

    joined = bd / "reel_joined.mp4"
    concat(parts, joined, bd)

    body_srt = bd / f"{ctx.topic_id}_reel.srt"
    write_srt(cues, body_srt)

    # 16:9 content is letterboxed into the middle of the 9:16 frame: running captions go
    # in the black band below it, the burned hook line in the band above it.
    band = (r["height"] - r["width"] * 9 // 16) // 2
    hook = [{"start": 0.0, "end": 4.0, "text": REEL_HOOK}]
    hook_track = render_caption_track(hook, 4.0, r["width"], r["height"], r["fps"],
                                      bd / "reelhook", bd / "reel_hook.mov", cfg,
                                      font_size=52, margin_bottom=r["height"] - band + 40,
                                      emoji=REEL_HOOK_EMOJI)
    # Clamp to what was actually concatenated. cursor is the sum of the intended
    # segment lengths; if anything downstream shortens the join, a caption would
    # otherwise hang over the outro card.
    real = ffprobe_duration(joined)
    cues = [c for c in cues if c["start"] < real]
    for c in cues:
        c["end"] = min(c["end"], real)
    body_track = render_caption_track(cues, real, r["width"], r["height"], r["fps"],
                                      bd / "reelcap", bd / "reel_caps.mov", cfg,
                                      font_size=44, margin_bottom=band // 3)
    _run(["ffmpeg", "-v", "error", "-i", str(joined), "-i", str(body_track),
          "-i", str(hook_track),
          "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[a];"
                             "[a][2:v]overlay=0:0:eof_action=pass[v]",
          "-map", "[v]", "-map", "0:a",
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "copy", "-movflags", "+faststart", "-y", str(ctx.reel_mp4)])

    return {"seconds": round(ffprobe_duration(ctx.reel_mp4), 2), "picks": picked,
            "cues": len(cues)}
