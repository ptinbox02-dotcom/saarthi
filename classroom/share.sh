#!/usr/bin/env bash
# Put the classroom on a public URL, from this machine, in one command.
#
#   bash classroom/share.sh [passcode] [port]
#
# A Cloudflare quick tunnel needs no account, so this is the shortest path from "it
# runs on my laptop" to "here is a link". The trade is that the link lives only as long
# as this machine is awake and these two processes are running, and the URL is new every
# time. For a link that outlives the laptop, see DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${2:-8756}"
PASS="${1:-saarthi-$(python3 -c 'import secrets;print(secrets.token_hex(3))')}"

command -v cloudflared >/dev/null || { echo "install cloudflared first: brew install cloudflared"; exit 1; }

pkill -f "[c]lassroom/server/app.py" 2>/dev/null || true
pkill -f "[c]loudflared tunnel --url" 2>/dev/null || true
sleep 1

SAARTHI_PASSCODE="$PASS" SAARTHI_DAILY_ASKS="${SAARTHI_DAILY_ASKS:-200}" \
  nohup bash classroom/run.sh "$PORT" > /tmp/saarthi-server.log 2>&1 &
for _ in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/gate" || true)" = "200" ] && break
  sleep 1
done

nohup cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
  > /tmp/saarthi-tunnel.log 2>&1 &

URL=""
for _ in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/saarthi-tunnel.log | head -1 || true)
  [ -n "$URL" ] && break
  sleep 2
done
[ -n "$URL" ] || { echo "the tunnel did not come up; see /tmp/saarthi-tunnel.log"; exit 1; }

# Confirm the gate is closed from the outside before handing over the link — a public
# URL on a billed key is not something to take on trust.
OPEN=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$URL/api/lessons")
[ "$OPEN" = "401" ] || { echo "REFUSING: /api/lessons answered $OPEN from outside, expected 401"; exit 1; }

printf '\n  link      %s\n  passcode  %s\n  budget    %s questions today\n\n' \
  "$URL" "$PASS" "${SAARTHI_DAILY_ASKS:-200}"
echo "  Stop it with:  pkill -f 'cloudflared tunnel --url'; pkill -f classroom/server/app.py"
