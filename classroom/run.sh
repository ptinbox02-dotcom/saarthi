#!/usr/bin/env bash
# Launch the classroom server with the project's venv.
# `python classroom/server/app.py` uses the system Python, which has none of the deps.
set -euo pipefail
cd "$(dirname "$0")/.."                    # repo root: media paths resolve from here
PY="../micro-lectures/.venv/bin/python"
[[ -x "$PY" ]] || { echo "!! venv missing at $PY"; exit 1; }
"$PY" -c "import aiohttp" 2>/dev/null || { echo "installing aiohttp…"; "$PY" -m pip install -q aiohttp; }
PYTHONUNBUFFERED=1 exec "$PY" classroom/server/app.py "${1:-8756}"
