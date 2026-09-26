#!/usr/bin/env python3
"""Voice audition — pick the Gemini TTS voice for the Hinglish mentor.

Judged on the two things that actually matter here and can be measured:
  1. intelligibility  — faster-whisper round-trip WER on the Hinglish line (objective)
  2. persona fit      — Gemini listens to the clip and rates Indian-accent authenticity,
                        Hinglish code-switch naturalness and warmth (subjective, but a
                        consistent rater across all candidates)
Writes build/audition/<Voice>.wav so a human can confirm the winner by ear.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google.genai import types

from gclient import JUDGE, KIT, client, retry
from tts import GeminiTTS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "audition"

# A line that stresses everything the lesson needs: Hindi matrix, English technical
# terms, a proper noun and the mentor's direct address.
LINE = ("Tumhare ghar mein jo tube light roz jalti hai, us ek tube ke andar chhupi hai "
        "physics ki sabse badi khoj. Socho zara — jo gas bijli rok rahi thi, wahi ab glow "
        "kar rahi hai. Ek aisa particle jise aaj tak kisi ne dekha nahi. Naam? Electron.")

# Warm / informative / friendly male-leaning voices from the 30-voice roster.
CANDIDATES = ["Charon", "Orus", "Iapetus", "Rasalgethi", "Achird",
              "Sadaltager", "Algieba", "Umbriel", "Schedar", "Sulafat"]

RUBRIC = """You are auditioning a voice for an Indian JEE physics micro-lecture aimed at
students across India. Listen to the attached audio.

Rate each 1-5 (5 best):
- indian_accent: does this sound like a natural Indian English speaker (NOT American,
  British or a generic neutral accent)? 5 = clearly, authentically Indian.
- hinglish: does the Hindi-English code-switching sound fluent and unforced, with the
  Hindi words pronounced correctly rather than read phonetically by an English speaker?
- warmth: warm, encouraging mentor tone — a favourite teacher, not a newsreader.
- clarity: are the English technical terms crisply enunciated?

Also report any audible defect (clipping, robotic artefacts, wrong language, added words,
odd pacing) in `defects`, and give a one-sentence `note`.

Return ONLY JSON:
{"indian_accent":n,"hinglish":n,"warmth":n,"clarity":n,"defects":"...","note":"..."}"""


def judge_audio(c, wav: Path) -> dict:
    part = types.Part.from_bytes(data=wav.read_bytes(), mime_type="audio/wav")
    r = retry(lambda: c.models.generate_content(
        model=JUDGE, contents=[part, RUBRIC],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    ), what=f"judge {wav.name}")
    return json.loads(r.text)


def wer_of(wav: Path, reference: str):
    """Phonetic CER, not raw WER — the factory's gate metric.

    Whisper returns Hinglish as Devanagari, so raw WER against a Roman reference
    measures orthography rather than speech. evals.phonetic_cer folds both sides to a
    consonant skeleton, which is what config's audio_wer_max: 0.15 is calibrated against.
    """
    sys.path.insert(0, str(KIT))
    from evals import phonetic_cer, transcribe
    heard = transcribe(wav)
    return phonetic_cer(reference, heard), heard


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c = client()
    rows = []
    for v in CANDIDATES:
        wav = OUT / f"{v}.wav"
        try:
            tts = GeminiTTS(voice=v, cache_dir=OUT / "_cache")
            src = tts.synth(LINE)
            wav.write_bytes(src.read_bytes())
        except Exception as e:  # noqa: BLE001
            print(f"{v:14s} SYNTH FAIL {str(e)[:90]}")
            continue
        try:
            j = judge_audio(c, wav)
        except Exception as e:  # noqa: BLE001
            print(f"{v:14s} JUDGE FAIL {str(e)[:90]}")
            continue
        cer, heard = wer_of(wav, LINE)
        persona = (j["indian_accent"] * 2 + j["hinglish"] * 2 + j["warmth"] + j["clarity"]) / 6
        rows.append({"voice": v, **j, "cer": round(cer, 3), "persona": round(persona, 2),
                     "heard": heard[:160]})
        print(f"{v:14s} accent={j['indian_accent']} hinglish={j['hinglish']} "
              f"warmth={j['warmth']} clarity={j['clarity']} persona={persona:.2f} "
              f"CER={cer:.3f}  {j['note'][:70]}")

    rows.sort(key=lambda r: (-r["persona"], r["cer"]))
    (OUT / "audition.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print("\n=== ranking (persona desc, then CER) ===")
    for r in rows:
        print(f"  {r['voice']:14s} persona={r['persona']:.2f} CER={r['cer']:.3f} "
              f"accent={r['indian_accent']} defects={r['defects'][:50]}")
    if rows:
        print(f"\nWINNER: {rows[0]['voice']}")


if __name__ == "__main__":
    sys.exit(main())
