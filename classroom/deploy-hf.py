#!/usr/bin/env python3
"""Put the classroom on a free Hugging Face Space.

Spaces are the one host that is genuinely free with no card, runs a Dockerfile as-is,
and allows the WebSocket the tutor needs. Everything here runs after `hf auth login`,
which needs an interactive terminal and so cannot be scripted.

    python3 classroom/deploy-hf.py [--name saarthi-classroom]

The passcode is generated and printed once. The Gemini key is read from .env and set as
a Space secret — it is never written into the repo and never printed.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STAGE = Path("/tmp/saarthi-space")


def stage() -> Path:
    """Build exactly what the image needs, and nothing else."""
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "classroom").mkdir(parents=True)
    (STAGE / "classroom").mkdir(parents=True)
    shutil.copy(REPO / "classroom" / "Dockerfile", STAGE / "Dockerfile")
    shutil.copytree(REPO / "core", STAGE / "core",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(REPO / "classroom" / "requirements.txt",
                STAGE / "classroom" / "requirements.txt")
    for d in ("server", "web", "lessons", "media"):
        shutil.copytree(REPO / "classroom" / d, STAGE / "classroom" / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.bak",
                                                      "_fx_*.json", "*.pyc"))
    (STAGE / "README.md").write_text((REPO / "classroom" / "SPACE_README.md").read_text())
    size = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    print(f"  staged {size / 1e6:.0f} MB")
    return STAGE


def gemini_key() -> str:
    m = re.search(r"^GEMINI_API_KEY\s*=\s*(\S+)", (REPO / ".env").read_text(), re.M)
    if not m:
        sys.exit("  GEMINI_API_KEY is not in ml-gemini-hybrid/.env")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="saarthi-classroom")
    ap.add_argument("--passcode", default=None)
    args = ap.parse_args()

    from huggingface_hub import HfApi
    api = HfApi()
    try:
        who = api.whoami()["name"]
    except Exception:
        sys.exit("  Not logged in. Run:  hf auth login")
    repo_id = f"{who}/{args.name}"
    passcode = args.passcode or f"saarthi-{secrets.token_hex(3)}"
    print(f"  deploying as {who} → {repo_id}")

    api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker",
                    exist_ok=True, private=False)

    # Secrets before the upload, so the very first build already has them and the Space
    # never boots once without a passcode.
    api.add_space_secret(repo_id, "GEMINI_API_KEY", gemini_key())
    api.add_space_secret(repo_id, "SAARTHI_PASSCODE", passcode)
    api.add_space_variable(repo_id, "SAARTHI_DAILY_ASKS", "200")
    print("  secrets set (key never printed)")

    api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(stage()),
                      commit_message="Saarthi classroom")
    url = f"https://huggingface.co/spaces/{repo_id}"
    live = f"https://{who}-{args.name}".lower().replace("_", "-") + ".hf.space"
    print(f"\n  building: {url}")

    # A Space that answers 200 before its build has finished is serving the old image,
    # so wait on the runtime stage rather than on the URL.
    for _ in range(120):
        stage_now = api.get_space_runtime(repo_id).stage
        print(f"  {stage_now}")
        if stage_now == "RUNNING":
            break
        if stage_now in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR"):
            print(f"  build failed — logs at {url}?logs=build")
            return 1
        time.sleep(10)

    print(f"\n  link      {live}\n  passcode  {passcode}\n  budget    200 questions/day")
    return 0


if __name__ == "__main__":
    sys.exit(main())
