#!/usr/bin/env python3
"""Shared Gemini client + resolved model names for the hybrid build.

Model names are resolved once here (verified against the live key in step 0) so that a
model rename is a one-line change instead of a grep across the pipeline.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai

ROOT = Path(__file__).resolve().parent.parent
MICRO = ROOT.parent / "micro-lectures"

# --- resolved models (verified live on this key, step 0) --------------------
JUDGE = "gemini-3.6-flash"                    # text + image + video in, text out
# 24kHz 16-bit mono PCM out. Overridable per run, because the binding constraint on
# this pipeline is not price but a PER-MODEL DAILY REQUEST CAP that enabling billing
# does NOT raise: gemini-2.5-pro-tts stays at 50/day on a paid key. One lesson costs
# ~35 requests, so a second lesson the same day must be voiced on a different model.
# Achird was auditioned on gemini-3.1-flash-tts-preview, so that is the default.
# Fallback order, highest quality first. RPD is per MODEL, so the usable daily budget
# is the sum of the row caps, not the largest one:
#     gemini-2.5-pro-preview-tts     50 / day
#     gemini-3.1-flash-tts-preview  100 / day
#     gemini-2.5-flash-preview-tts  100 / day
# Tier 1 does apply — 50 is simply Pro TTS's paid ceiling, because TTS is billed as a
# multimodal generative model and does not scale with the tier the way text models do
# (2.5 Pro text went 25 -> 1000 RPD on the same upgrade).
TTS_CHAIN = [
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
]
TTS = os.environ.get("TTS_MODEL", TTS_CHAIN[0])
VIDEO = "veo-3.1-fast-generate-preview"       # predictLongRunning; 4/6/8s, 720p/1080p, 16:9|9:16

# Veo hard limits, from the docs — enforced in step 3 rather than discovered at runtime.
VEO_DURATIONS = (4, 6, 8)
VEO_RESOLUTIONS = ("720p", "1080p")
VEO_ASPECTS = ("16:9", "9:16")


def topic() -> str:
    """Which lesson this run builds. Everything else derives its paths from here."""
    return os.environ.get("TOPIC", "ML1.1")


def lesson_dirs() -> dict:
    """Per-topic working directories, so several lessons can coexist."""
    t = topic()
    return {"topic": t,
            "script": MICRO / "lessons" / t / "script.md",
            "facts": MICRO / "lessons" / t / "fact_sheet.md",
            "scenes": ROOT / "scenes" / t,
            "audio": ROOT / "audio" / t,
            "manim": ROOT / "manim" / t,
            "build": ROOT / "build" / t}


def load_keys() -> dict:
    """Read .env without ever logging a value."""
    load_dotenv(ROOT / ".env", override=True)
    return {k: v for k, v in os.environ.items() if k.endswith("_API_KEY")}


def client() -> genai.Client:
    load_keys()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing from ml-gemini-hybrid/.env")
    return genai.Client(api_key=key)


def retry(fn, tries: int = 5, base: float = 4.0, what: str = "call"):
    """Backoff for 429/503 — both are routine on preview models under load."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - SDK raises a wide range
            last = e
            msg = str(e)
            # Status-code strings cover API-level failures, but a dropped connection
            # never gets that far: httpx raises RemoteProtocolError / ConnectError /
            # ReadTimeout with no code in the message, and those are the most transient
            # failures of all. Matching on the exception type as well stops a single
            # dropped socket from ending a 30-request lesson.
            transient = (
                any(c in msg for c in ("429", "503", "500", "502", "504", "UNAVAILABLE"))
                or type(e).__name__ in ("RemoteProtocolError", "ConnectError",
                                        "ConnectTimeout", "ReadTimeout", "ReadError",
                                        "WriteError", "PoolTimeout", "ProtocolError")
            )
            if not transient or i == tries - 1:
                raise
            time.sleep(base * (i + 1))
    raise RuntimeError(f"{what} failed: {last}")


def must_hear(cfg: dict) -> list[str]:
    """Terms the narration has to contain, per topic.

    config.yaml's list is ML1.1's vocabulary; using it for any other lesson would fail
    the gate on words that lesson never says. topics.yaml is the per-topic override.
    """
    import yaml
    t = topic()
    tp = yaml.safe_load((MICRO / "factory" / "topics.yaml").read_text())
    for row in tp.get("topics", []):
        if row.get("id") == t and row.get("must_hear"):
            return [str(x) for x in row["must_hear"]]
    return list(cfg.get("voice", {}).get("must_hear", []))


def parse_json(text: str) -> dict:
    """Parse a judge reply that is *supposed* to be pure JSON.

    response_mime_type="application/json" is a strong hint, not a guarantee: the model
    occasionally appends prose after the closing brace, and a bare json.loads() then
    raises "Extra data" and takes the whole run down with it. Decoding just the first
    object tolerates that without silently accepting garbage.
    """
    import json as _json
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        obj, _ = _json.JSONDecoder().raw_decode(text[start:])
        return obj
