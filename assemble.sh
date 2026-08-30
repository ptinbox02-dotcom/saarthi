#!/usr/bin/env bash
# Assemble ML 1.1.
#
# This script used to fit B-roll clips to narration and burn overlay text on them. That
# design was cut after review: the generated B-roll read as stock-footage mood rather
# than physics teaching (see build/COMPARISON.md), so the lesson is now Manim end to
# end, rebuilt in a 3Blue1Brown visual grammar under scenes/.
#
# The real work lives in hb/step4_assemble.py, which reuses the micro-lectures factory's
# assembler (per-beat fit, brand intro/outro, SRT, burned captions, 9:16 reel) instead of
# reimplementing it here. The previous ffmpeg version is kept as assemble.sh.orig.
#
# Usage:  bash assemble.sh
# Needs:  audio/*.wav      (hb/step1_audio.py)
#         manim/beatNN.mp4 (hb/step2_manim.py)
set -euo pipefail
cd "$(dirname "$0")"

PY="../micro-lectures/.venv/bin/python"
[[ -x "$PY" ]] || { echo "!! missing venv at $PY"; exit 1; }

for d in audio manim; do
  compgen -G "$d/*" > /dev/null || { echo "!! $d/ is empty — run the earlier steps first"; exit 1; }
done

exec "$PY" hb/step4_assemble.py "$@"
