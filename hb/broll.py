#!/usr/bin/env python3
"""STEP 3 — B-roll (Veo 3.1 fast), with the text-free gate.

The accuracy rule this file exists to protect: generated video NEVER carries a fact. So
the judge's hard criterion is not "does it look nice" but "is there any readable text in
frame". A clip with legible text is rejected and regenerated even if it is beautiful,
because ffmpeg burns every label later and a garbled Veo caption would be a fact the
lesson did not sanction.

Veo limits enforced here (verified step 0): duration 4/6/8s, 720p|1080p, 16:9|9:16,
audio always generated (stripped at assembly), files retained server-side 2 days.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from google.genai import types

from gclient import (JUDGE, VEO_ASPECTS, VEO_DURATIONS, VEO_RESOLUTIONS, VIDEO,
                     client, parse_json, retry)

ROOT = Path(__file__).resolve().parent.parent
BROLL = ROOT / "broll"

NEGATIVE = ("text, letters, words, captions, subtitles, watermark, logo, signage, "
            "numbers, writing, typography, user interface, on-screen graphics")


@dataclass
class Shot:
    name: str
    seconds: int
    prompt: str
    intent: str          # what the judge checks the shot actually shows


# Prompts are omni_prompts.md verbatim in intent, with the text-free instruction made
# explicit for the model as well as for the judge.
SHOTS = [
    Shot("b1_tubelight", 6,
         "A modest middle-class Indian home at dusk, warm and lived-in. A ceiling "
         "fluorescent tube light flickers a couple of times with that familiar tink-tink "
         "start, then settles into a steady cool-white glow that fills the room. Slow "
         "push-in toward the glowing tube. Cinematic, shallow depth of field, gentle film "
         "grain, moody dark surroundings. No text anywhere in frame, no signage, no "
         "people's faces in focus.",
         "a fluorescent tube light switching on and glowing inside an Indian home at dusk"),
    Shot("b2_tubelight_closeup", 4,
         "Extreme macro close-up of a glowing fluorescent tube light: the soft blue-white "
         "mercury glow along the glass, subtle shimmer, dust motes drifting in the light. "
         "Very shallow focus, dark background. Slow drift along the tube. No text "
         "anywhere in frame.",
         "an extreme macro of a glowing fluorescent tube, blue-white glow, dark background"),
    Shot("b3_wind_leaves", 4,
         "A quiet Indian courtyard: a gust of wind moves green peepal leaves and a hanging "
         "dupatta; you cannot see the wind, only its effect on the things it moves. Warm "
         "afternoon light, cinematic, shallow depth of field, dark shaded background. No "
         "text anywhere in frame.",
         "wind moving leaves and hanging cloth in an Indian courtyard"),
    Shot("b4_thomson_lab", 6,
         "A late-19th-century physics laboratory, Cambridge-era: dark wooden benches, brass "
         "and glass apparatus, a glass vacuum tube apparatus faintly glowing, warm "
         "lamplight, motes of dust in shafts of light. Slow dolly across the bench. "
         "Atmospheric, moody, cinematic. No modern objects. No text, no labels, no readable "
         "writing anywhere in frame.",
         "a dim 19th-century physics laboratory with brass and glass apparatus, lamplight"),
]

RUBRIC = """You are the accuracy gate for an educational physics video.

The attached clip is atmospheric B-roll. Every factual label in the lesson is added later
in ffmpeg, so this clip must carry NO information of its own.

Intended shot: {intent}

Judge strictly and return ONLY JSON:
{{"shows_intended_shot": true/false,
  "readable_text": true/false,      // ANY legible letters, words, numbers, signage,
                                     // watermark or caption visible in ANY frame
  "text_evidence": "what text you saw, or empty",
  "dark_cinematic": true/false,     // dark/moody, shallow depth of field, cuts cleanly
                                     // against a #0e1116 background
  "faces_in_focus": true/false,
  "quality": n,                      // 1-5 overall production feel
  "note": "one sentence"}}

Be conservative on readable_text: if you can make out letterforms at all, say true."""


def strip_audio(src: Path, dst: Path) -> None:
    """Veo always generates audio; the lesson's only audio source is the TTS track."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-an",
                    "-c:v", "copy", str(dst)], check=True)


# Re-rolls vary the prompt instead of the seed: the Developer API rejects `seed`
# ("only supported in Gemini Enterprise Agent Platform mode"), so a retry with an
# identical prompt would just re-draw from the same distribution with no steering.
# Each variation also pushes harder on whatever the judge rejected.
REROLL = [
    "",
    " Framing slightly wider, with more of the dark surroundings visible. Absolutely no "
    "text, signage or writing of any kind anywhere in the frame.",
    " Tighter framing on the light source itself, heavier bokeh, deeper shadows. The "
    "frame must contain no letters, numbers, signs, labels or writing at all.",
    " Abstract and minimal: mostly darkness with the glow as the only subject, no "
    "background detail that could contain writing. Zero text in frame.",
]


def generate(c, shot: Shot, attempt: int = 0,
             resolution: str = "1080p", aspect: str = "16:9") -> Path:
    # Veo 3.1: 1080p is only offered at the full 8-second length. Everything is therefore
    # generated at 8s/1080p and fitted to its segment at assembly (trim, or a mild
    # setpts stretch) — that also beats upscaling a 720p source into a 1080p master.
    seconds = 8 if resolution == "1080p" else shot.seconds
    assert seconds in VEO_DURATIONS, f"Veo duration must be one of {VEO_DURATIONS}"
    assert resolution in VEO_RESOLUTIONS and aspect in VEO_ASPECTS

    cfg = types.GenerateVideosConfig(
        aspect_ratio=aspect, resolution=resolution, duration_seconds=seconds,
        negative_prompt=NEGATIVE, person_generation="allow_all",
        number_of_videos=1,
    )
    prompt = shot.prompt + REROLL[min(attempt, len(REROLL) - 1)]

    op = retry(lambda: c.models.generate_videos(model=VIDEO, prompt=prompt, config=cfg),
               what=f"veo {shot.name}")
    t0 = time.time()
    while not op.done:
        if time.time() - t0 > 900:
            raise RuntimeError(f"{shot.name}: Veo operation timed out after 15 min")
        time.sleep(10)
        op = c.operations.get(op)
    if getattr(op, "error", None):
        raise RuntimeError(f"{shot.name}: {op.error}")

    gv = op.response.generated_videos[0]
    c.files.download(file=gv.video)
    raw = BROLL / f"_raw_{shot.name}.mp4"
    raw.parent.mkdir(parents=True, exist_ok=True)
    gv.video.save(str(raw))
    return raw


def judge(c, clip: Path, shot: Shot) -> dict:
    part = types.Part.from_bytes(data=clip.read_bytes(), mime_type="video/mp4")
    r = retry(lambda: c.models.generate_content(
        model=JUDGE, contents=[part, RUBRIC.format(intent=shot.intent)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ), what=f"judge {clip.name}")
    return parse_json(r.text)


def verdict(j: dict) -> tuple[bool, str]:
    """readable_text is a hard fail — it is the accuracy gate, not a style preference."""
    if j.get("readable_text"):
        return False, f"readable text in frame: {j.get('text_evidence', '')!r}"
    if not j.get("shows_intended_shot"):
        return False, f"wrong shot: {j.get('note', '')}"
    if not j.get("dark_cinematic"):
        return False, f"not dark/cinematic: {j.get('note', '')}"
    if int(j.get("quality", 0)) < 3:
        return False, f"quality {j.get('quality')}/5: {j.get('note', '')}"
    return True, j.get("note", "")


def make(shot: Shot, max_retries: int = 3, resolution: str = "1080p") -> dict:
    c = client()
    attempts = []
    billed = 0                      # only clips Veo actually rendered are charged;
                                    # a 400 rejection costs nothing
    for attempt in range(max_retries + 1):
        try:
            raw = generate(c, shot, attempt=attempt, resolution=resolution)
            billed += 8 if resolution == "1080p" else shot.seconds
        except Exception as e:  # noqa: BLE001
            attempts.append({"attempt": attempt, "error": str(e)[:300]})
            print(f"  {shot.name} attempt {attempt}: GENERATE FAIL {str(e)[:120]}")
            continue
        j = judge(c, raw, shot)
        ok, why = verdict(j)
        attempts.append({"attempt": attempt, "judge": j, "passed": ok, "reason": why})
        print(f"  {shot.name} attempt {attempt}: {'PASS' if ok else 'FAIL'} "
              f"text={j.get('readable_text')} shot={j.get('shows_intended_shot')} "
              f"dark={j.get('dark_cinematic')} q={j.get('quality')} — {why[:80]}")
        if ok:
            final = BROLL / f"{shot.name}.mp4"
            strip_audio(raw, final)
            raw.unlink(missing_ok=True)
            return {"shot": shot.name, "ok": True, "path": str(final),
                    "seconds": 8 if resolution == "1080p" else shot.seconds,
                    "resolution": resolution, "attempts": attempts,
                    "billed_seconds": billed}
        raw.unlink(missing_ok=True)
    return {"shot": shot.name, "ok": False, "attempts": attempts,
            "billed_seconds": billed}


def main():
    only = sys.argv[1:] or None
    shots = [s for s in SHOTS if not only or s.name in only]
    BROLL.mkdir(parents=True, exist_ok=True)
    results = []
    for s in shots:
        print(f"[3] {s.name} ({s.seconds}s)")
        results.append(make(s))
    report = BROLL / "broll_report.json"
    prev = json.loads(report.read_text()) if report.exists() else []
    by = {r["shot"]: r for r in prev}
    by.update({r["shot"]: r for r in results})
    report.write_text(json.dumps(list(by.values()), indent=2, ensure_ascii=False))

    billed = sum(r["billed_seconds"] for r in results)
    print(f"\n{sum(1 for r in results if r['ok'])}/{len(results)} passed; "
          f"{billed}s billed")


if __name__ == "__main__":
    sys.exit(main())
