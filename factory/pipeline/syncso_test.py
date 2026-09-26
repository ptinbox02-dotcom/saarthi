#!/usr/bin/env python3
"""Lip-sync A/B: Sync.so's sync-2 against the HeyGen avatar.

HeyGen generates a talking avatar FROM audio. Sync.so does the opposite job: it
re-drives the mouth of an EXISTING video to match supplied audio. That difference is
why it is worth testing here — the complaint is lip-sync fidelity on Hinglish, and a
dedicated lipsync model on real presenter footage is a different bet from a synthetic
avatar's built-in viseme model.

Uses the teacher footage already on the account rather than the HeyGen output, so the
test measures Sync.so's lipsync rather than Sync.so re-syncing another model's mouth.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

from gclient import ROOT, lesson_dirs

API = "https://api.sync.so/v2"


def key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SYNCSO_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SYNCSO_API_KEY missing")


def upload(k: str, path: Path, content_type: str) -> str:
    """Presigned PUT, then register. /v2/assets takes a URL, not a file body."""
    h = {"x-api-key": k, "Content-Type": "application/json"}
    r = requests.post(f"{API}/assets/upload", headers=h, timeout=60, json={
        "fileName": path.name, "contentType": content_type,
        "size": path.stat().st_size})
    r.raise_for_status()
    d = r.json()
    put_url = d.get("uploadUrl") or d.get("url") or d.get("presignedUrl")
    if not put_url:
        raise RuntimeError(f"no upload url in {d}")
    up = requests.put(put_url, data=path.read_bytes(),
                      headers={"Content-Type": content_type}, timeout=600)
    up.raise_for_status()
    return d.get("assetUrl") or d.get("fileUrl") or put_url.split("?")[0]


def main():
    k = key()
    dirs = lesson_dirs()
    wav = dirs["audio"] / "b0.wav"

    assets = requests.get(f"{API}/assets", headers={"x-api-key": k},
                          timeout=60).json()["items"]
    video = next((a for a in assets if a["name"] == "teacher.mp4"), None) \
        or next((a for a in assets if a.get("type") == "VIDEO"), None)
    if not video:
        print("no presenter video on the account"); return 1
    print(f"  source video : {video['name']} ({video['size'] / 1048576:.1f} MB)")

    print(f"  uploading    : {wav.name} ({wav.stat().st_size / 1048576:.1f} MB)")
    audio_url = upload(k, wav, "audio/wav")
    print(f"  audio asset  : {audio_url[:70]}…")

    body = {
        "model": "sync-2",
        "input": [{"type": "video", "url": video["url"]},
                  {"type": "audio", "url": audio_url}],
        # the narration is longer than the source clip, so the video must repeat
        # rather than the audio being truncated
        "options": {"sync_mode": "loop"},
    }
    r = requests.post(f"{API}/generate", headers={"x-api-key": k,
                                                  "Content-Type": "application/json"},
                      json=body, timeout=120)
    if r.status_code >= 300:
        print("  generate failed:", r.status_code, r.text[:300]); return 1
    gid = r.json()["id"]
    print(f"  job          : {gid}")

    t0 = time.time()
    while time.time() - t0 < 1800:
        g = requests.get(f"{API}/generations/{gid}", headers={"x-api-key": k},
                         timeout=60).json()
        st = g["status"]
        if st == "COMPLETED":
            out = dirs["build"] / "lipsync" / "syncso_b0.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(requests.get(g["outputUrl"], timeout=600).content)
            print(f"  COMPLETED in {(time.time() - t0) / 60:.1f} min -> {out}")
            print(f"  duration: {g.get('outputDuration')}s")
            return 0
        if st in ("FAILED", "REJECTED", "CANCELLED", "TIMED_OUT"):
            print(f"  {st}: {g.get('error')} / {g.get('errorCode')}"); return 1
        print(f"    {st} ({(time.time() - t0) / 60:.1f} min)")
        time.sleep(20)
    print("  timed out"); return 1


if __name__ == "__main__":
    sys.exit(main())
