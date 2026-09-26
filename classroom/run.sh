#!/usr/bin/env bash
# Launch the classroom server.
#
#   bash classroom/run.sh [port]
#
# Finds a usable interpreter rather than assuming one. This script used to hardcode
# ../micro-lectures/.venv — a path outside the repository that resolved on exactly one
# laptop. A fresh clone anywhere else cloned cleanly, contained every file, and could
# not start. The code was restorable; the project was not.
set -euo pipefail
cd "$(dirname "$0")/.."                    # repo root: media paths resolve from here
PORT="${1:-8756}"

# Returns 0 either way: under `set -e`, a function whose last test fails takes the
# whole script down, and the only symptom is an empty log.
pick() {
  for c in "${SAARTHI_PYTHON:-}" ".venv/bin/python" "../micro-lectures/.venv/bin/python"; do
    [[ -n "$c" && -x "$c" ]] && { echo "$c"; return 0; }
  done
  return 0
}

PY="$(pick)"
if [[ -z "$PY" ]]; then
  echo "  no environment found — creating .venv"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r classroom/requirements.txt
  PY=".venv/bin/python"
fi

"$PY" -c "import aiohttp, google.genai" 2>/dev/null || {
  echo "  installing dependencies into $PY"
  "$PY" -m pip install -q -r classroom/requirements.txt
}

[[ -f .env || -n "${GEMINI_API_KEY:-}" ]] || cat <<'MSG'
  !! No .env and no GEMINI_API_KEY in the environment.
     The lessons and the board will work; the tutor will not.
     Put GEMINI_API_KEY=... in .env at the repo root, or export it.
MSG

PYTHONUNBUFFERED=1 exec "$PY" classroom/server/app.py "$PORT"
