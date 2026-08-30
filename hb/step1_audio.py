#!/usr/bin/env python3
"""STEP 1 — Audio (Gemini TTS), one wav per hybrid segment, with the pronunciation gate.

Gate (config.yaml evals.audio_wer_max = 0.15), measured exactly as the pure-Manim lesson
measures it: faster-whisper transcribes each spoken chunk in isolation and the reference
is compared by phonetic CER, not raw WER — Whisper returns Hinglish as Devanagari, so a
raw word comparison against Roman text would score orthography instead of speech.

On a failing chunk the fix is a re-roll of that chunk, not a respelling: Gemini TTS is
sampled, and rewriting the words would change what the lesson says.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

from gclient import MICRO, lesson_dirs, must_hear
from segments import Segment, segments
from tts import STYLE, GeminiTTS, QuotaExhausted, prepare

sys.path.insert(0, str(MICRO / "factory"))
from audio import chunk_narration, splice  # noqa: E402
from evals import phonetic_cer, transcribe_chunk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
AUDIO = DIRS["audio"]
BUILD = DIRS["build"]
CFG = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())

VOICE = "Achird"          # audition winner (Borda, stable across both prompt orderings)
MAX_CHUNK_RETRIES = 2

# A chunk is only RE-ROLLED when it is clearly wrong, not merely over the reporting
# threshold. Marginal scores here are orthography, not speech: Whisper writes spoken
# numbers back as digits ("one point seven six" -> "1.76") and transliterates English
# loanwords ("glow" -> the Devanagari spelling), both of which cost CER without any
# mispronunciation. Re-rolling those burned four whisper passes per chunk for nothing
# and stalled the 128-second beat for over an hour. Anything above RETRY_CER really is
# garbled and is worth another take.
RETRY_CER = 0.30
SKIP_GATE = os.environ.get("SKIP_GATE") == "1"


def coalesce(specs: list[dict], inter_ms: int, max_chars: int = 700) -> list[dict]:
    """Merge sentences that are only separated by the inter-sentence gap.

    The factory synthesised one request per SENTENCE because Sarvam was billed per
    character and the splice gave exact timing. Gemini TTS is billed per REQUEST and
    capped at 100/day/model, and this lesson has ~114 sentences — so a request per
    sentence cannot finish at all. Gemini also reads multi-sentence text with correct
    sentence prosody on its own, so the only silences that must be spliced by hand are
    the deliberate ones: [PAUSE] and the ellipsis. Those are preserved exactly; the
    120 ms inter-sentence gaps are handed back to the model.

    Merging is capped at max_chars so no single request has to carry a minute of
    continuous speech, which is where pace drift and truncation start to show up.
    """
    out: list[dict] = []
    for spec in specs:
        if (spec["kind"] == "silence" and spec["ms"] <= inter_ms
                and out and out[-1]["kind"] == "speech"):
            out.append({"kind": "pending_merge"})
            continue
        if (spec["kind"] == "speech" and len(out) >= 2
                and out[-1]["kind"] == "pending_merge"):
            merged = out[-2]["text"].rstrip() + " " + spec["text"].lstrip()
            if len(merged) <= max_chars:
                out.pop()
                out[-1] = {"kind": "speech", "text": merged}
                continue
            # Too long to be one take: keep the sentence break as a real (short)
            # silence rather than asking the model for a minute of speech in one go,
            # where it tends to drift in pace and occasionally truncate.
            out[-1] = {"kind": "silence", "ms": inter_ms}
            out.append(spec)
            continue
        out.append(spec)
    return [s for s in out if s["kind"] != "pending_merge"]


def synth_segment_gated(tts: GeminiTTS, seg: Segment, tmpdir: Path) -> dict:
    v, thr = CFG["voice"], CFG["evals"]["audio_wer_max"]
    spoken = prepare(seg.narration, list(v.get("pronunciation") or []))
    specs = chunk_narration(spoken,
                            int(v.get("pause_long_ms", 900)),
                            int(v.get("pause_short_ms", 400)),
                            int(v.get("inter_sentence_ms", 120)))
    specs = coalesce(specs, int(v.get("inter_sentence_ms", 120)))

    pairs, retries = [], []
    for i, s in enumerate(specs):
        if s["kind"] != "speech":
            pairs.append((s, None))
            continue
        if SKIP_GATE:
            # Synthesis only. The gate still runs, but as a separate pass over the
            # finished wavs (gate_audio.py) so a 30-minute whisper queue on one long
            # beat cannot block the renders and the assembly behind it.
            pairs.append((s, tts.synth(s["text"])))
            continue
        best, best_cer, best_heard = None, 9.9, ""
        for nonce in range(MAX_CHUNK_RETRIES + 1):
            wav = tts.synth(s["text"], nonce=nonce)
            heard = transcribe_chunk(wav, 0.0, 1e6,
                                     tmpdir / f"{seg.sid}_{i:03d}_{nonce}.wav")
            cer = phonetic_cer(s["text"], heard)
            if cer < best_cer:
                best, best_cer, best_heard = wav, cer, heard
            if cer <= RETRY_CER:
                break
        if best_cer > thr:
            retries.append({"chunk": i, "text": s["text"][:70],
                            "heard": best_heard[:70], "cer": round(best_cer, 3)})
        pairs.append((s, best))

    out = AUDIO / f"{seg.sid}.wav"
    manifest = splice(pairs, out)
    meta = {
        "sid": seg.sid, "beat": seg.beat, "part": seg.part, "engine": seg.engine,
        "broll": seg.broll, "overlay": seg.overlay,
        "wav": out.name, "wav_path": str(out), "voice": tts.voice, "model": tts.model,
        "duration": manifest[-1]["end"] if manifest else 0.0,
        "spoken_text": spoken, "chunks": manifest, "bad_chunks": retries,
    }
    (AUDIO / f"{seg.sid}.chunks.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    tmpdir = BUILD / "_stt"
    tmpdir.mkdir(parents=True, exist_ok=True)
    tts = GeminiTTS(voice=VOICE, cache_dir=AUDIO / "chunks", style=STYLE)

    segs = segments()
    metas, total = [], 0.0
    while True:
        try:
            metas, total = [], 0.0
            for seg in segs:
                meta = synth_segment_gated(tts, seg, tmpdir)
                metas.append(meta)
                total += meta["duration"]
                flag = (f"  !! {len(meta['bad_chunks'])} chunk(s) over gate"
                        if meta["bad_chunks"] else "")
                print(f"  [1] {seg.sid:4s} {meta['duration']:6.1f}s  "
                      f"{len(meta['chunks']):2d} chunks  {seg.engine:5s}{flag}")
            break
        except QuotaExhausted as e:
            # Restart the WHOLE lesson on the next model rather than splicing two
            # voices together. Chunks already synthesised on the old model stay
            # cached, so nothing is lost if its quota returns tomorrow.
            print(f"  [1] {e} ran dry mid-lesson — restarting on the next model")
            if not tts.advance():
                print("  [1] every TTS model is out of daily quota"); return 1
            print(f"  [1] now voicing on {tts.model}")

    # whole-lesson checks
    full = " ".join(m["spoken_text"] for m in metas)
    must = [t.lower() for t in must_hear(CFG)]
    missing = [t for t in must if t not in full.lower()]

    bad = [(m["sid"], m["bad_chunks"]) for m in metas if m["bad_chunks"]]
    report = {
        "voice": VOICE, "model": tts.model, "segments": len(metas),
        "narration_seconds": round(total, 1),
        "must_hear_missing": missing,
        "segments_with_bad_chunks": bad,
        "tts_calls": tts.calls,
        "metas": metas,
    }
    (BUILD / "audio_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))

    print(f"\n  narration total {total:.1f}s ({total / 60:.1f} min) over {len(metas)} segments")
    print(f"  must_hear missing: {missing or 'none'}")
    print(f"  segments with chunks over CER gate: {[s for s, _ in bad] or 'none'}")
    print(f"  new TTS calls: {tts.calls}")
    # tmpdir is kept: transcribe_chunk caches transcripts there by audio hash, so a
    # re-run of the gate on unchanged narration is seconds instead of half an hour.
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
