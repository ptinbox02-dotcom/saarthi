#!/usr/bin/env python3
"""Voice audition pack — pick the Bulbul speaker by ear, then lock it in config.yaml.

    python factory/voicecheck.py            # write lessons/_voice_samples/*.wav
    python factory/voicecheck.py --pick rahul --spelling devanagari

Accent and warmth cannot be measured, so this renders the same passage across
candidate male voices and across both spellings of the technical terms (Latin vs
Devanagari), as ONE request each — so what you hear is also the prosody continuity a
whole beat will have. An STT round-trip is printed next to each so a voice that sounds
fine but garbles "cathode" is visible too.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audio import SarvamTTS, prepare_tts_text
from common import CFG_PATH, ROOT, ffprobe_duration, load_yaml

CANDIDATES = ["aditya", "rahul", "dev", "ashutosh", "anand"]

# one passage that exercises every failure mode the review flagged:
# technical terms, a year, a decimal constant, a power, and the mentor register.
PASSAGE = (
    "Ek sirā cathode — yaani negative terminal, minus. Doosra anode — positive, plus. "
    "1897 mein J.J. Thomson ne in rays ko naapa, aur nikala charge-to-mass ratio — "
    "yaani e/m. Value aayi lagbhag 1.758 × 10¹¹ coulomb per kilogram. "
    "Ab yahaan dhyaan do — yahi galti exam mein le doobti hai: Thomson ne e ya m "
    "alag-alag nahi naapa. Electron ka mass hydrogen atom se 1836 guna halka hai. "
    "Socho zara — ek tube light se poora atom samajh aa gaya."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speakers", default=",".join(CANDIDATES))
    ap.add_argument("--stt", action="store_true", help="also run the STT round-trip")
    args = ap.parse_args()

    cfg = load_yaml(CFG_PATH)
    v = dict(cfg["voice"])
    outdir = ROOT / "lessons" / "_voice_samples"
    outdir.mkdir(parents=True, exist_ok=True)

    variants = {
        "latin": {**v, "devanagari_terms": {}},
        "devanagari": v,
    }
    rows = []
    for speaker in args.speakers.split(","):
        for name, vv in variants.items():
            cfg2 = {**cfg, "voice": {**vv, "speaker_id": speaker}}
            tts = SarvamTTS(cfg2, outdir / ".cache")
            text = prepare_tts_text(PASSAGE, vv)
            wav = tts.synth(text, float(v.get("pace", 1.0)))
            dest = outdir / f"{speaker}__{name}.wav"
            dest.write_bytes(wav.read_bytes())
            rows.append((speaker, name, dest, text))
            print(f"  {dest.name:34s} {ffprobe_duration(dest):5.1f}s")

    (outdir / "SPOKEN_TEXT.md").write_text(
        "# What the voice was asked to say\n\n"
        + "\n\n".join(f"## {s} · {n}\n\n```\n{t}\n```" for s, n, _, t in rows[:2])
    )

    if args.stt:
        import evals
        print("\nSTT round-trip (phonetic CER, lower is better):")
        for speaker, name, dest, text in rows:
            hyp = evals.transcribe(dest)
            print(f"  {speaker:10s} {name:11s} CER {evals.phonetic_cer(text, hyp):.3f}")

    print(f"\n{len(rows)} samples in {outdir}")
    print("Listen, then set voice.speaker_id (and keep/clear voice.devanagari_terms) "
          "in factory/config.yaml.")


if __name__ == "__main__":
    main()
