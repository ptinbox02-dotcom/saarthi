#!/usr/bin/env python3
"""Gemini TTS backend for the hybrid build (replaces Sarvam/Bulbul).

Why this shape: Gemini TTS, like Bulbul, has no SSML and no <break> tag — style is
steered in natural language instead. So the factory's chunk-and-splice architecture is
kept verbatim (imported from ../micro-lectures/factory/audio.py): narration is split at
[PAUSE] / … / sentence boundaries, each chunk is synthesised alone, and exact digital
silence is spliced between chunks. That gives sample-accurate pauses AND the per-chunk
timing manifest the SRT captions and the reel cut are built from.

What changes vs Sarvam: one call. Gemini returns raw PCM (24kHz/16-bit/mono) rather than
a wav container, so we wrap it before splicing. Voice and accent come from a style
preamble that is NOT spoken (the model treats "Say ...:" as an instruction).
"""
from __future__ import annotations

import hashlib
import re
import sys
import wave
from pathlib import Path

from google.genai import types

from gclient import TTS as TTS_MODEL, TTS_CHAIN, MICRO, client, retry

sys.path.insert(0, str(MICRO / "factory"))

# Reuse the factory's text prep + splicing untouched — same behaviour as the pure-Manim
# lesson, so the A/B comparison is not confounded by a different chunker.
from audio import (  # noqa: E402
    apply_pronunciation,
    chunk_narration,
    englishify_numbers,
    splice,
)

class QuotaExhausted(RuntimeError):
    """The model this lesson is being voiced on ran out of daily quota mid-lesson."""


SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1

# The persona. Sent as an instruction prefix on every chunk so the accent stays stable
# across a 6-minute lesson; it is never spoken aloud.
STYLE = (
    "You are a warm, encouraging Indian physics mentor teaching JEE students in India. "
    "Speak in a natural Indian English accent, code-switching fluently and unselfconsciously "
    "between Hindi and English exactly as written (Hinglish). Keep English technical terms "
    "crisp and clearly enunciated. Unhurried, friendly, like a favourite teacher explaining "
    "to one student. Do not add any words of your own. Say exactly this:"
)


def pcm_to_wav(pcm: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return path


class GeminiTTS:
    """Chunk-level synthesiser with an on-disk cache keyed by voice+style+text."""

    def __init__(self, voice: str, cache_dir: Path, style: str = STYLE,
                 model: str = TTS_MODEL, chain: list[str] | None = None):
        self.voice = voice
        self.style = style
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.c = client()
        self.calls = 0
        # Daily caps are per model, so a lesson that would not fit one model's budget
        # can still finish by rolling to the next. The chain is probed ONCE up front
        # rather than on first failure: switching model mid-lesson changes the voice,
        # and a seam in the middle of a beat is worse than starting on a lesser model.
        self.chain = [m for m in (chain or [model] + [x for x in TTS_CHAIN if x != model])]
        self.model = self._first_with_quota()

    def _first_with_quota(self) -> str:
        for i, m in enumerate(self.chain):
            try:
                self._probe(m)
                if i:
                    print(f"    [tts] {self.chain[0]} is out of daily quota; "
                          f"voicing this lesson on {m}")
                return m
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                # 429 = out of daily quota, move on. 503 = the model is briefly
                # overloaded; retry() has already backed off, so treat a persistent one
                # as "unavailable right now" and try the next model rather than failing
                # the whole lesson on a transient spike.
                if not any(t in msg for t in ("RESOURCE_EXHAUSTED", "429",
                                              "UNAVAILABLE", "503")):
                    raise
                print(f"    [tts] {m} unavailable ({msg[:60]})")
        raise RuntimeError("every TTS model in the chain is out of daily quota")

    def advance(self) -> bool:
        """Move to the next model in the chain. False when the chain is exhausted."""
        i = self.chain.index(self.model)
        for m in self.chain[i + 1:]:
            try:
                self._probe(m)
                self.model = m
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _probe(self, model: str):
        return retry(lambda: self.c.models.generate_content(
            # A bare token reads as a text prompt to some TTS models and is rejected
            # with "Model tried to generate text". The probe must look like the real
            # request: an explicit instruction to speak a short transcript.
            model=model, contents="Say exactly this:\n\nnamaste",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.voice))),
            ),
        ), tries=3, what=f"probe {model}")

    def synth(self, text: str, nonce: int = 0) -> Path:
        """nonce > 0 forces a fresh take of the same words.

        Gemini TTS is sampled, so a chunk that came back slurred or mispronounced is
        usually fixed by re-rolling it rather than by respelling the text — and
        respelling would change what the lesson says. The nonce keys a separate cache
        entry and nudges the delivery instruction without touching the words.
        """
        h = hashlib.sha1(
            f"{self.model}|{self.voice}|{self.style}|{nonce}|{text}".encode()
        ).hexdigest()[:16]
        out = self.cache / f"{h}.wav"
        if out.exists() and out.stat().st_size > 1000:
            return out

        style = self.style
        if nonce:
            style += (f" Enunciate every word clearly and at a measured pace "
                      f"(take {nonce + 1}).")

        def call():
            nonlocal style
            return self.c.models.generate_content(
                model=self.model,
                contents=f"{style}\n\n{text}",
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.voice
                            )
                        )
                    ),
                ),
            )

        # The model occasionally returns a perfectly successful response with an empty
        # candidate (finish_reason OTHER, content None). retry() cannot see this: there
        # is no HTTP error to match on. It is transient — the identical chunk succeeds
        # on the next roll — so re-sample here rather than failing a whole lesson at
        # beat 7 with two thirds of the narration already paid for.
        pcm = None
        for attempt in range(4):
            try:
                r = retry(call, what=f"TTS {text[:40]!r}")
            except Exception as e:  # noqa: BLE001
                # A model can pass the opening probe and still run dry part-way through
                # a lesson. Rolling to the next model right here would put a voice seam
                # mid-beat, so surface it and let the caller restart the whole lesson on
                # the next model instead.
                if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                    raise QuotaExhausted(self.model) from e
                raise
            cand = r.candidates[0] if r.candidates else None
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            for part in parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    pcm = part.inline_data.data
                    break
            if pcm:
                break
            reason = getattr(cand, "finish_reason", "?")
            print(f"    [tts] empty audio (finish={reason}), re-rolling "
                  f"{attempt + 1}/4: {text[:50]!r}")
            style = self.style + f" (take {attempt + 2})"
        if not pcm:
            raise RuntimeError(f"TTS returned no audio after 4 tries for {text[:60]!r}")
        self.calls += 1
        return pcm_to_wav(pcm, out)


def prepare(text: str, rules: list[dict]) -> str:
    """script.md narration -> exactly what the model is asked to say."""
    text = apply_pronunciation(text, rules or [])
    text = englishify_numbers(text)
    return re.sub(r"[ \t]{2,}", " ", text)


def synth_segment(tts: GeminiTTS, text: str, out_wav: Path, rules: list[dict],
                  pause_long_ms: int, pause_short_ms: int, inter_sentence_ms: int) -> dict:
    """Synthesise one hybrid segment (a beat, or half of a split beat)."""
    spoken = prepare(text, rules)
    specs = chunk_narration(spoken, pause_long_ms, pause_short_ms, inter_sentence_ms)
    pairs = [(s, tts.synth(s["text"]) if s["kind"] == "speech" else None) for s in specs]
    manifest = splice(pairs, out_wav)
    return {
        "wav": out_wav.name,
        "voice": tts.voice,
        "model": tts.model,
        "duration": manifest[-1]["end"] if manifest else 0.0,
        "spoken_text": spoken,
        "chunks": manifest,
    }
