#!/usr/bin/env python3
"""STEP 8 — Audio (Sarvam TTS / Bulbul).

Sarvam's /text-to-speech endpoint has no SSML and no break tag (verified against
docs.sarvam.ai at build time), so pauses are produced by *chunking*: the narration is
split at [PAUSE] / … / sentence boundaries, each chunk is synthesised on its own, and
digital silence of the configured length is spliced between chunks with sample
accuracy. That also hands step 9 an exact per-chunk timing manifest, which is what the
SRT captions and the reel cut are built from — no forced alignment needed.

Artifacts per beat:
  audio/beatNN.wav          – the beat's narration, pauses included
  audio/beatNN.chunks.json  – [{i, text, start, end, kind}] in beat-local seconds
  audio/chunks/*.wav        – per-chunk cache (keyed by text+voice+pace hash)
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import wave
from pathlib import Path

import requests

from common import load_env

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# masks that survive sentence splitting
_DOT = "\x03"
_PAUSE = "\x01"
_ELLIPSIS = "\x02"

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=\S)")


# ----- numbers spoken in English -------------------------------------------
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
         "ninety"]


def int_to_english(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + int_to_english(rest))
    for div, name in ((1_000_000, "million"), (1000, "thousand")):
        if n >= div:
            rest = n % div
            return (int_to_english(n // div) + " " + name
                    + ("" if rest == 0 else " " + int_to_english(rest)))
    return str(n)


def year_to_english(y: int) -> str:
    """1897 -> 'eighteen ninety seven'; 1900 -> 'nineteen hundred'; 2007 -> 'two thousand seven'."""
    hi, lo = divmod(y, 100)
    if 2000 <= y < 2010:
        return "two thousand" + ("" if lo == 0 else " " + int_to_english(lo))
    if lo == 0:
        return int_to_english(hi) + " hundred"
    if lo < 10:
        return int_to_english(hi) + " oh " + _ONES[lo]
    return int_to_english(hi) + " " + int_to_english(lo)


SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def englishify_numbers(text: str) -> str:
    """Say every year, decimal and quantity in English inside Hindi narration.

    hi-IN Bulbul otherwise reads 1897 as "attharah sau santaanve" and 1.758 in Hindi,
    which is not how a JEE class says a date or a constant. Explicit config rules run
    before this, so a lesson can override any specific number (1836 is a ratio, not a
    year, and is spelled out in its rule).
    """
    text = text.translate(SUPERSCRIPT)
    # powers first: "10 to the power 11"
    text = re.sub(r"\b10\s*\^?\s*(\d{1,2})\b",
                  lambda m: "ten to the power " + int_to_english(int(m.group(1))), text)
    text = re.sub(r"\b(\d{1,3}(?:,\d{3})+)\b",
                  lambda m: int_to_english(int(m.group(1).replace(",", ""))), text)
    text = re.sub(r"\b(\d+)\.(\d+)\b",
                  lambda m: (int_to_english(int(m.group(1))) + " point "
                             + " ".join(_ONES[int(d)] for d in m.group(2))), text)
    text = re.sub(r"\b(1[5-9]\d\d|20\d\d)\b",
                  lambda m: year_to_english(int(m.group(1))), text)
    text = re.sub(r"\b(\d{1,6})\b", lambda m: int_to_english(int(m.group(1))), text)
    return text


def devanagarify(text: str, terms: dict) -> str:
    """Respell English technical terms in Devanagari for the TTS input only.

    A hi-IN voice pronounces a Latin-script "cathode" inconsistently; written as
    कैथोड it lands reliably on the Indian-English pronunciation a JEE student hears
    in class. On-screen text and captions stay in Latin.
    """
    for eng, dev in (terms or {}).items():
        text = re.sub(rf"\b{re.escape(eng)}\b", dev, text, flags=re.I)
    return text


# ----- text preparation ----------------------------------------------------
def apply_pronunciation(text: str, rules: list[dict]) -> str:
    """Step-8 pronunciation notes: e/m -> 'e-by-m', 10¹¹ -> '10 to the power 11', …

    Rules are data (config.yaml voice.pronunciation + any hints the eval feeds back),
    so a failing STT round-trip can add a hint and re-run without touching code.
    """
    for r in rules or []:
        find, repl = r["find"], r["replace"]
        if r.get("regex"):
            text = re.sub(find, repl, text)
        else:
            text = text.replace(find, repl)
    return re.sub(r"[ \t]{2,}", " ", text)


def prepare_tts_text(text: str, cfg_voice: dict, extra_rules: list[dict] | None = None) -> str:
    """script.md narration -> exactly what Bulbul is asked to say."""
    text = apply_pronunciation(text, list(cfg_voice.get("pronunciation") or [])
                               + list(extra_rules or []))
    if cfg_voice.get("numbers_in_english", True):
        text = englishify_numbers(text)
    table = cfg_voice.get("devanagari_terms") or {}
    if cfg_voice.get("use_devanagari_terms", False):
        text = devanagarify(text, table)
    else:
        # even with the global respelling off, individual terms the STT gate has caught
        # being mangled get forced — that is the phonetic-hint loop the gate feeds.
        forced = {k: table[k] for k in (cfg_voice.get("forced_devanagari") or [])
                  if k in table}
        text = devanagarify(text, forced)
    return re.sub(r"[ \t]{2,}", " ", text)


def split_sentences(text: str) -> list[str]:
    t = re.sub(r"(\d)\.(\d)", rf"\1{_DOT}\2", text)
    t = re.sub(r"\b([A-Z])\.", rf"\1{_DOT}", t)
    return [p.replace(_DOT, ".").strip()
            for p in SENT_SPLIT.split(t) if p.strip()]


def quiet_gaps(wav_path) -> tuple[list[float], float] | None:
    """Every quiet gap in a wav, as midpoint times, plus the total duration.

    Sentences are synthesised inside one request now (see synth_beat), so their
    boundaries are no longer known from the splice arithmetic. Measuring the pauses in
    the rendered audio recovers them, and costs nothing: it is a pass over the samples,
    not another model.
    """
    import numpy as np
    with wave.open(str(wav_path), "rb") as w:
        fr, n, sw, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(n)
    if sw != 2:
        return None
    x = np.frombuffer(raw, dtype="<i2").astype("float32")
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    hop = max(1, int(fr * 0.02))
    frames = x[:len(x) // hop * hop].reshape(-1, hop)
    rms = np.sqrt((frames ** 2).mean(1)) + 1e-9
    quiet = rms < max(rms.max() * 0.02, np.percentile(rms, 20))

    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(quiet)))
    dur = len(x) / fr
    # ignore leading/trailing silence and anything too short to be a sentence break
    runs = [(a, b) for a, b in runs
            if (b - a) * 0.02 >= 0.10 and a * 0.02 > 0.15 and b * 0.02 < dur - 0.15]
    return [(a + b) / 2 * 0.02 for a, b in runs], dur


def sentence_bounds(wav_path, sents: list[str], run_len: float) -> list[float]:
    """Where each sentence ends inside a multi-sentence run.

    Taking the n-1 *longest* gaps is wrong: TTS often pauses longer at a comma or a
    dash than at some full stops, so the longest gaps are not necessarily the sentence
    ends, and one bad pick shifts every caption after it. Instead each boundary is
    predicted from cumulative character count and then snapped to the nearest real gap,
    in order — the text says roughly where the break is, the audio says exactly.
    """
    n = len(sents)
    total = sum(len(x) for x in sents)
    acc, expected = 0, []
    for x in sents[:-1]:
        acc += len(x)
        expected.append(run_len * acc / total)
    if n < 2:
        return []

    found = quiet_gaps(wav_path)
    if not found:
        return expected
    gaps, _ = found
    window = max(0.35, 0.12 * run_len)
    out, used, prev = [], set(), 0.0
    for e in expected:
        best, best_d = None, window
        for j, g in enumerate(gaps):
            if j in used or g <= prev + 0.05:
                continue
            if abs(g - e) < best_d:
                best, best_d = j, abs(g - e)
        if best is None:
            out.append(max(e, prev + 0.05))
        else:
            used.add(best)
            out.append(gaps[best])
        prev = out[-1]
    return out


def chunk_narration(text: str, pause_long_ms: int, pause_short_ms: int,
                    inter_sentence_ms: int = 120) -> list[dict]:
    """Split narration into speech chunks separated by explicit silences."""
    t = text.replace("[PAUSE]", f" {_PAUSE} ").replace("…", f" {_ELLIPSIS} ")
    # protect decimals and initials from the sentence splitter
    t = re.sub(r"(\d)\.(\d)", rf"\1{_DOT}\2", t)
    t = re.sub(r"\b([A-Z])\.", rf"\1{_DOT}", t)

    out: list[dict] = []

    def emit_speech(s: str):
        s = s.replace(_DOT, ".").strip()
        s = re.sub(r"\s+", " ", s)
        if s:
            out.append({"kind": "speech", "text": s})

    def emit_silence(ms: int):
        if out and out[-1]["kind"] == "silence":
            out[-1]["ms"] = max(out[-1]["ms"], ms)
        else:
            out.append({"kind": "silence", "ms": ms})

    for token in re.split(rf"([{_PAUSE}{_ELLIPSIS}])", t):
        if token == _PAUSE:
            emit_silence(pause_long_ms)
        elif token == _ELLIPSIS:
            emit_silence(pause_short_ms)
        else:
            parts = [p for p in SENT_SPLIT.split(token) if p.strip()]
            for i, p in enumerate(parts):
                emit_speech(p)
                if i < len(parts) - 1:
                    emit_silence(inter_sentence_ms)

    # never start or end a beat on silence
    while out and out[0]["kind"] == "silence":
        out.pop(0)
    while out and out[-1]["kind"] == "silence":
        out.pop()
    return out


# ----- Sarvam ---------------------------------------------------------------
class SarvamTTS:
    def __init__(self, cfg: dict, cache_dir: Path):
        v = cfg["voice"]
        env = load_env()
        self.key = env.get("SARVAM_API_KEY")
        if not self.key:
            raise RuntimeError("SARVAM_API_KEY missing from .env")
        self.model = v.get("model_id") or "bulbul:v3"
        self.speaker = v.get("speaker_id") or "aditya"
        self.language = v.get("language", "hi-IN")
        self.sample_rate = int(v.get("sample_rate", 24000))
        self.temperature = v.get("temperature")
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def synth(self, text: str, pace: float) -> Path:
        h = hashlib.sha1(
            f"{self.model}|{self.speaker}|{self.language}|{pace}|{self.sample_rate}"
            f"|{self.temperature}|{text}".encode()
        ).hexdigest()[:16]
        out = self.cache / f"{h}.wav"
        if out.exists() and out.stat().st_size > 1000:
            return out
        body = {
            "text": text,
            "language_code": self.language,
            "speaker": self.speaker,
            "model": self.model,
            "pace": round(float(pace), 2),
            "speech_sample_rate": self.sample_rate,
            "output_audio_codec": "wav",
        }
        if self.temperature is not None:
            body["temperature"] = float(self.temperature)
        last = None
        for attempt in range(4):
            try:
                r = requests.post(
                    SARVAM_TTS_URL,
                    headers={"api-subscription-key": self.key,
                             "Content-Type": "application/json"},
                    json=body, timeout=180,
                )
                if r.status_code == 200:
                    audio = base64.b64decode(r.json()["audios"][0])
                    out.write_bytes(audio)
                    self.calls += 1
                    return out
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            except requests.RequestException as e:      # transient network
                last = str(e)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"Sarvam TTS failed for {text[:60]!r}: {last}")


# ----- wav splicing ---------------------------------------------------------
def _read(p: Path):
    with wave.open(str(p), "rb") as w:
        return w.getparams(), w.readframes(w.getnframes())


def splice(chunk_paths: list[tuple[dict, Path | None]], out_path: Path) -> list[dict]:
    """Concatenate chunk wavs with silences; return the timing manifest."""
    params = None
    for spec, p in chunk_paths:
        if p is not None:
            params, _ = _read(p)
            break
    if params is None:
        raise RuntimeError("no speech chunks to splice")

    frames = bytearray()
    manifest = []
    fr = params.framerate
    bytes_per_frame = params.sampwidth * params.nchannels

    for i, (spec, p) in enumerate(chunk_paths):
        start = len(frames) / bytes_per_frame / fr
        if spec["kind"] == "silence":
            n = int(fr * spec["ms"] / 1000.0)
            frames += b"\x00" * (n * bytes_per_frame)
        else:
            _, data = _read(p)
            frames += data
        end = len(frames) / bytes_per_frame / fr
        manifest.append({"i": i, "kind": spec["kind"],
                         "text": spec.get("text", ""),
                         "start": round(start, 3), "end": round(end, 3)})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))
    return manifest


# ----- the step -------------------------------------------------------------
def split_runs(text: str, pause_long_ms: int, pause_short_ms: int) -> list[dict]:
    """Break narration ONLY at [PAUSE] and … — each run is one TTS request.

    Synthesising sentence-by-sentence restarts the model's prosody on every full stop,
    which is what made the mentor's personality drift within a beat. A run is spoken in
    one breath, so intonation carries across its sentences; the only silences inserted
    are the ones the script actually asks for.
    """
    t = text.replace("[PAUSE]", f" {_PAUSE} ").replace("…", f" {_ELLIPSIS} ")
    out: list[dict] = []
    for token in re.split(rf"([{_PAUSE}{_ELLIPSIS}])", t):
        if token == _PAUSE:
            out.append({"kind": "silence", "ms": pause_long_ms})
        elif token == _ELLIPSIS:
            out.append({"kind": "silence", "ms": pause_short_ms})
        else:
            s = re.sub(r"\s+", " ", token).strip()
            if s:
                out.append({"kind": "speech", "text": s})
    while out and out[0]["kind"] == "silence":
        out.pop(0)
    while out and out[-1]["kind"] == "silence":
        out.pop()
    # a run longer than the model's limit still has to be broken somewhere
    capped = []
    for r in out:
        if r["kind"] == "speech" and len(r["text"]) > 2000:
            sents, cur = split_sentences(r["text"]), ""
            for s in sents:
                if len(cur) + len(s) > 1800 and cur:
                    capped.append({"kind": "speech", "text": cur.strip()}); cur = ""
                cur += " " + s
            if cur.strip():
                capped.append({"kind": "speech", "text": cur.strip()})
        else:
            capped.append(r)
    return capped


def display_sentences(beat_text: str, cfg_voice: dict) -> list[str]:
    """The same sentences, spelled for the eye instead of for the voice.

    The TTS text says "eighteen ninety seven" and may respell terms in Devanagari;
    burning that into a caption looks amateurish — a caption should read "1897". None of
    the TTS transforms add or remove a sentence or a pause, so the two versions split
    into the same structure and can be paired position by position.
    """
    runs = split_runs(beat_text, int(cfg_voice.get("pause_long_ms", 900)),
                      int(cfg_voice.get("pause_short_ms", 400)))
    out = []
    for r in runs:
        if r["kind"] == "speech":
            out.extend(split_sentences(r["text"]))
    return out


def refine_to_sentences(manifest: list[dict], pairs) -> list[dict]:
    """Re-cut each spoken run into sentence-level manifest entries.

    Step 9 builds captions and the reel cut from this manifest, so it still needs
    sentence granularity even though the audio is now rendered a run at a time. The
    boundaries come from the quiet gaps in the run's own audio, so they stay exact.
    """
    out = []
    for entry, (spec, path) in zip(manifest, pairs):
        if entry["kind"] != "speech" or path is None:
            out.append(entry)
            continue
        sents = split_sentences(entry["text"])
        if len(sents) < 2:
            out.append(entry)
            continue
        run_len = entry["end"] - entry["start"]
        cuts = sentence_bounds(path, sents, run_len)
        edges = [0.0] + [min(c, run_len) for c in cuts] + [run_len]
        for s, a, b in zip(sents, edges, edges[1:]):
            out.append({"i": len(out), "kind": "speech", "text": s,
                        "start": round(entry["start"] + a, 3),
                        "end": round(entry["start"] + b, 3)})
    for i, e in enumerate(out):
        e["i"] = i
    return out


def synth_beat(tts: SarvamTTS, beat, cfg: dict, audio_dir: Path,
               pace: float, extra_rules: list[dict]) -> dict:
    v = cfg["voice"]
    spoken = prepare_tts_text(beat.narration_tts, v, extra_rules)
    runs = split_runs(spoken,
                      int(v.get("pause_long_ms", 900)),
                      int(v.get("pause_short_ms", 400)))
    pairs = []
    for s in runs:
        pairs.append((s, tts.synth(s["text"], pace) if s["kind"] == "speech" else None))

    wav = audio_dir / f"{beat.slug}.wav"
    manifest = splice(pairs, wav)
    manifest = refine_to_sentences(manifest, pairs)

    # captions read the script's spelling (1897), not the voice's (eighteen ninety seven)
    disp = display_sentences(beat.narration_tts, v)
    spoken_sents = [c for c in manifest if c["kind"] == "speech"]
    if len(disp) == len(spoken_sents):
        for c, d in zip(spoken_sents, disp):
            c["spoken"] = c["text"]
            c["text"] = d
    else:
        print(f"  [8] {beat.slug}: caption/spoken sentence counts differ "
              f"({len(disp)} vs {len(spoken_sents)}) — captions keep the spoken spelling")
    meta = {
        "beat": beat.index,
        "slug": beat.slug,
        "wav": wav.name,
        "pace": pace,
        "speaker": tts.speaker,
        "model": tts.model,
        "duration": manifest[-1]["end"] if manifest else 0.0,
        "spoken_text": spoken,
        "chunks": manifest,
    }
    (audio_dir / f"{beat.slug}.chunks.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
