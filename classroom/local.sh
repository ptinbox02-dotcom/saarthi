#!/usr/bin/env bash
# Run the classroom on this machine and print the address a phone on the same wifi
# should use.
#
#   bash classroom/local.sh [port]
#
# No passcode by default — this is your own network. Set SAARTHI_PASSCODE if you are
# on wifi you do not control.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8756}"

pkill -f "[c]lassroom/server/app.py" 2>/dev/null || true
sleep 1
nohup bash classroom/run.sh "$PORT" > /tmp/saarthi-server.log 2>&1 &

for _ in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/api/lessons" || true)" = "200" ] && break
  sleep 1
done

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
printf '\n  on this Mac    http://localhost:%s\n' "$PORT"
[ -n "$IP" ] && printf '  on your phone  http://%s:%s     <- paste this into the app\n' "$IP" "$PORT"
printf '\n  stop it with:  pkill -f classroom/server/app.py\n'
