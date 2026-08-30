#!/usr/bin/env python3
"""Copy the lesson media in beside the app, so the deployment is self-contained.

The manifests hold absolute paths from the machine each lesson was rendered on. Those
do not exist anywhere else, so before shipping, the videos are copied to
`classroom/media/<topic>/<file>` — which is where the server looks when the recorded
path is missing.

    python3 classroom/pack.py            # copy what the manifests point at
    python3 classroom/pack.py --check    # report only, copy nothing
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEDIA = ROOT / "media"


def main(check_only: bool) -> int:
    total, missing = 0, []
    for man_path in sorted((ROOT / "lessons").glob("*.json")):
        man = json.loads(man_path.read_text())
        topic = man["topic"]
        for key in ("video_path", "reel_path"):
            src = man.get(key)
            if not src:
                continue
            src = Path(src)
            dst = MEDIA / topic / src.name
            if dst.exists() and dst.stat().st_size == (src.stat().st_size
                                                       if src.exists() else -1):
                print(f"  = {topic}/{src.name}  already packed")
                total += dst.stat().st_size
                continue
            if not src.exists():
                if dst.exists():
                    print(f"  = {topic}/{src.name}  packed (source is gone)")
                    total += dst.stat().st_size
                else:
                    missing.append(f"{topic}: {src}")
                continue
            if check_only:
                print(f"  + {topic}/{src.name}  {src.stat().st_size / 1e6:.1f} MB")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"  + {topic}/{src.name}  {dst.stat().st_size / 1e6:.1f} MB")
            total += src.stat().st_size

    print(f"\n  {total / 1e6:.0f} MB of media"
          f"{' would be packed' if check_only else ' packed into classroom/media/'}")
    for m in missing:
        print(f"  !! missing: {m}")
    if missing:
        print("\n  Re-render those lessons, or drop them from classroom/lessons/.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
