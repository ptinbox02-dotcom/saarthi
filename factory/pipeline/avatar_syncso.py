#!/usr/bin/env python3
"""Avatar layer via Sync.so (VISUAL_GRAMMAR.md §3), as an alternative to HeyGen.

Different mechanism, same output contract: HeyGen *generates* a talking avatar from
audio; Sync.so *re-drives the mouth* of real presenter footage to match audio. So this
needs a source video on the account, and produces a clip exactly as long as the
narration it was given — which is what the assembler's corner composite expects.

Cached by audio SHA like the HeyGen path, so a re-run never re-bills a beat whose
narration has not changed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import requests

from gclient import ROOT, lesson_dirs

API = "https://api.sync.so/v2"
MODEL = "sync-2"


def key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SYNCSO_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SYNCSO_API_KEY missing from .env")


def source_video(k: str, prefer="teacher.mp4") -> dict:
    items = requests.get(f"{API}/assets", headers={"x-api-key": k},
                         timeout=60).json()["items"]
    return (next((a for a in items if a["name"] == prefer), None)
            or next(a for a in items if a.get("type") == "VIDEO"))


def upload_audio(k: str, path: Path) -> str:
    h = {"x-api-key": k, "Content-Type": "application/json"}
    r = requests.post(f"{API}/assets/upload", headers=h, timeout=60, json={
        "fileName": path.name, "contentType": "audio/wav", "size": path.stat().st_size})
    r.raise_for_status()
    d = r.json()
    put = d.get("uploadUrl") or d.get("url") or d.get("presignedUrl")
    requests.put(put, data=path.read_bytes(),
                 headers={"Content-Type": "audio/wav"}, timeout=900).raise_for_status()
    return d.get("assetUrl") or d.get("fileUrl") or put.split("?")[0]


def generate(k: str, video_url: str, audio_url: str) -> str:
    r = requests.post(f"{API}/generate",
                      headers={"x-api-key": k, "Content-Type": "application/json"},
                      timeout=120, json={
                          "model": MODEL,
                          "input": [{"type": "video", "url": video_url},
                                    {"type": "audio", "url": audio_url}],
                          # narration outlasts the source clip, so repeat the footage
                          # rather than truncating the audio
                          "options": {"sync_mode": "loop"}})
    r.raise_for_status()
    return r.json()["id"]


def wait_and_fetch(k: str, gid: str, dest: Path, timeout=2400) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        g = requests.get(f"{API}/generations/{gid}", headers={"x-api-key": k},
                         timeout=60).json()
        if g["status"] == "COMPLETED":
            # the detail endpoint omits outputUrl; the list endpoint carries it with
            # the signed token, so read it from there
            lst = requests.get(f"{API}/generations", headers={"x-api-key": k},
                               timeout=60).json()
            url = next((x["outputUrl"] for x in lst if x["id"] == gid), None)
            if not url:
                print(f"    no outputUrl for {gid}"); return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(requests.get(url, timeout=900).content)
            return True
        if g["status"] in ("FAILED", "REJECTED", "CANCELLED", "TIMED_OUT"):
            print(f"    {g['status']}: {g.get('error')}"); return False
        time.sleep(20)
    return False


def main():
    sids = sys.argv[1:] or ["b0"]
    dirs = lesson_dirs()
    out_dir = dirs["build"] / "avatar"
    man_path = out_dir / "manifest_syncso.json"
    man = json.loads(man_path.read_text()) if man_path.exists() else {}

    k = key()
    vid = source_video(k)
    print(f"  source: {vid['name']} ({vid['size'] / 1048576:.1f} MB)")

    for sid in sids:
        wav = dirs["audio"] / f"{sid}.wav"
        if not wav.exists():
            print(f"  {sid}: no audio"); continue
        sha = hashlib.sha1(wav.read_bytes()).hexdigest()[:16]
        beat = f"beat{int(sid[1:].rstrip('abc') or 0):02d}"
        dest = out_dir / f"{beat}.mp4"
        if man.get(sid, {}).get("sha") == sha and dest.exists():
            print(f"  {sid}: cached"); continue
        try:
            au = upload_audio(k, wav)
            gid = generate(k, vid["url"], au)
            print(f"  {sid}: job {gid[:8]}… ({wav.stat().st_size / 1048576:.1f} MB audio)")
            if wait_and_fetch(k, gid, dest):
                man[sid] = {"sha": sha, "id": gid, "path": str(dest)}
                man_path.parent.mkdir(parents=True, exist_ok=True)
                man_path.write_text(json.dumps(man, indent=2))
                print(f"  {sid}: -> {dest.name} "
                      f"({dest.stat().st_size / 1048576:.1f} MB)")
        except Exception as e:  # noqa: BLE001 - avatar must never block a build
            print(f"  {sid}: FAILED {str(e)[:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
