#!/usr/bin/env python3
"""Audio pronunciation gate — run over the finished wavs.

Split out of step 1 deliberately. Transcribing every chunk with faster-whisper large-v3
on CPU takes far longer than synthesising it, and running it inline blocked the renders
and the assembly behind one long beat for over an hour. The gate is unchanged; only its
position in the pipeline moved.

Metric is the factory's phonetic CER against config's audio_wer_max, not raw WER:
Whisper returns Hinglish as Devanagari, so comparing words to a Roman reference would
score orthography rather than speech.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from gclient import MICRO, lesson_dirs, must_hear
from segments import segments

sys.path.insert(0, str(MICRO / "factory"))
from evals import phonetic_cer, transcribe_chunk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
AUDIO = DIRS["audio"]
BUILD = DIRS["build"]
CFG = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())


def main():
    thr = CFG["evals"]["audio_wer_max"]
    must = [t.lower() for t in must_hear(CFG)]
    tmp = BUILD / "_stt"
    tmp.mkdir(parents=True, exist_ok=True)

    rows, heard_all = [], []
    for seg in segments():
        meta_path = AUDIO / f"{seg.sid}.chunks.json"
        if not meta_path.exists():
            print(f"  {seg.sid}: missing audio"); return 1
        meta = json.loads(meta_path.read_text())
        wav = AUDIO / f"{seg.sid}.wav"

        pieces, worst = [], []
        for c in meta["chunks"]:
            if c["kind"] != "speech":
                continue
            h = transcribe_chunk(wav, c["start"], c["end"],
                                 tmp / f"{seg.sid}_{c['i']:03d}.wav")
            pieces.append(h)
            ccer = phonetic_cer(c["text"], h)
            if ccer > 0.30:
                worst.append({"text": c["text"][:70], "heard": h[:70],
                              "cer": round(ccer, 3)})
        hyp = " ".join(pieces)
        heard_all.append(hyp)
        cer = phonetic_cer(meta["spoken_text"], hyp)
        ok = cer <= thr
        rows.append({"sid": seg.sid, "cer": round(cer, 3), "passed": ok,
                     "bad_chunks": worst, "transcript": hyp})
        print(f"  {seg.sid:4s} CER={cer:.3f} {'PASS' if ok else 'FAIL'}"
              + (f"  bad_chunks={len(worst)}" if worst else ""))

    transcript = " ".join(heard_all).lower()
    missing = [t for t in must
               if t not in transcript and t not in " ".join(
                   json.loads((AUDIO / f"{s.sid}.chunks.json").read_text())["spoken_text"]
                   for s in segments()).lower()]

    failed = [r["sid"] for r in rows if not r["passed"]]
    report = {"threshold": thr, "segments": rows, "failed": failed,
              "must_hear_missing": missing}
    (BUILD / "audio_gate.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))

    print(f"\n  {len(rows) - len(failed)}/{len(rows)} segments within CER {thr}")
    print(f"  over threshold: {failed or 'none'}")
    print(f"  must_hear missing: {missing or 'none'}")
    return 0 if not failed and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
