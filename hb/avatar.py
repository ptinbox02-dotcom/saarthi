#!/usr/bin/env python3
"""Avatar layer (VISUAL_GRAMMAR.md §3) — HeyGen, lip-synced to the narration.

One avatar clip per beat, driven by that beat's narration wav, so the lip sync is exact
by construction and the clip length matches the beat without any time-warping.

Cost discipline: HeyGen bills ~1 credit per second of output, so every generated clip is
recorded in build/avatar/manifest.json keyed by the SHA of its audio. Re-running never
regenerates a beat whose narration has not changed — which matters, because a full pass
over this lesson is ~478 credits.

The lesson must still build with the avatar off (config avatar.enabled), so nothing here
is on the critical path: a missing clip degrades to no avatar, never to a failed build.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

from gclient import MICRO, lesson_dirs
from segments import segments

ROOT = Path(__file__).resolve().parent.parent
DIRS = lesson_dirs()
AUDIO = DIRS["audio"]
OUT = DIRS["build"] / "avatar"
CFG = yaml.safe_load((MICRO / "factory" / "config.yaml").read_text())

UPLOAD = "https://upload.heygen.com/v1/asset"
GENERATE = "https://api.heygen.com/v2/video/generate"
STATUS = "https://api.heygen.com/v1/video_status.get"
QUOTA = "https://api.heygen.com/v2/user/remaining_quota"

# "Vipin" — the custom avatar on this account: a real Indian presenter looking into
# camera, which is the eye-contact the teacher asked for. Stock avatars are mostly
# non-Indian and read as generic.
AVATAR_ID = "35f44180b68946899828aa40ba9bc6ca"


def key() -> str:
    """HeyGen key, read from the avatar demo project. Never logged."""
    env = ROOT.parent / "saarthi_avatar_cloud_demo" / "server" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("HEYGEN_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("HEYGEN_API_KEY not found")


def quota(k: str) -> int:
    r = requests.get(QUOTA, headers={"X-Api-Key": k}, timeout=30)
    return int(r.json()["data"]["remaining_quota"])


def upload_audio(k: str, wav: Path) -> str:
    # The endpoint validates the content type strictly: a .wav must be declared
    # audio/x-wav, not audio/wav, or it rejects with 400543.
    r = requests.post(UPLOAD, headers={"X-Api-Key": k, "Content-Type": "audio/x-wav"},
                      data=wav.read_bytes(), timeout=300)
    r.raise_for_status()
    return r.json()["data"]["id"]


def generate(k: str, asset_id: str, width=1280, height=720) -> str:
    body = {
        "video_inputs": [{
            "character": {"type": "avatar", "avatar_id": AVATAR_ID,
                          "avatar_style": "normal"},
            "voice": {"type": "audio", "audio_asset_id": asset_id},
        }],
        "dimension": {"width": width, "height": height},
    }
    r = requests.post(GENERATE, headers={"X-Api-Key": k,
                                         "Content-Type": "application/json"},
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()["data"]["video_id"]


def wait_for(k: str, video_id: str, timeout=1800) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = requests.get(STATUS, headers={"X-Api-Key": k},
                         params={"video_id": video_id}, timeout=40).json()["data"]
        if d["status"] == "completed":
            return d["video_url"]
        if d["status"] == "failed":
            raise RuntimeError(f"{video_id}: {d.get('error')}")
        time.sleep(15)
    raise RuntimeError(f"{video_id}: timed out")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    man_path = OUT / "manifest.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {}
    k = key()
    before = quota(k)
    print(f"  [av] credits before: {before}")

    only = set(sys.argv[1:]) or None
    for seg in segments():
        if only and seg.sid not in only:
            continue
        wav = AUDIO / f"{seg.sid}.wav"
        sha = hashlib.sha1(wav.read_bytes()).hexdigest()[:16]
        dest = OUT / f"{seg.beat_slug}.mp4"
        prev = man.get(seg.sid)
        if prev and prev.get("sha") == sha and dest.exists():
            print(f"  [av] {seg.sid}: cached ({dest.name})")
            continue
        try:
            asset = upload_audio(k, wav)
            vid = generate(k, asset)
            url = wait_for(k, vid)
            dest.write_bytes(requests.get(url, timeout=600).content)
            man[seg.sid] = {"sha": sha, "video_id": vid, "path": str(dest),
                            "beat": seg.beat}
            man_path.write_text(json.dumps(man, indent=2))
            print(f"  [av] {seg.sid}: {dest.name} "
                  f"({dest.stat().st_size / 1048576:.1f} MB)")
        except Exception as e:  # noqa: BLE001 - avatar must never block the build
            print(f"  [av] {seg.sid}: FAILED {str(e)[:140]}")

    after = quota(k)
    print(f"  [av] credits after: {after}  (spent {before - after})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
