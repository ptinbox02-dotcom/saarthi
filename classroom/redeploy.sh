#!/usr/bin/env bash
# Push and redeploy on Render.
#
#   RENDER_API_KEY=rnd_... bash classroom/redeploy.sh
#
# autoDeploy does not fire on this service. It was created from a public repo URL
# through the API, which installs no GitHub webhook, so Render never hears about a
# push — it will happily serve a commit from hours ago and report itself healthy.
# Connecting the GitHub app in the dashboard would fix that permanently; until then,
# this asks for the deploy explicitly and waits for it to go live.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RENDER_API_KEY:?set RENDER_API_KEY}"
SRV="${RENDER_SERVICE:-srv-daa52ghsrm7s73dvrdgg}"

git push origin main
HEAD_SHA=$(git rev-parse --short HEAD)
echo "  pushed $HEAD_SHA"

curl -s -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" -d '{"clearCache":"do_not_clear"}' \
  "https://api.render.com/v1/services/$SRV/deploys" > /dev/null
echo "  deploy requested"

for _ in $(seq 1 60); do
  read -r STATUS SHA < <(curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/$SRV/deploys?limit=1" \
    | python3 -c "import json,sys; d=json.load(sys.stdin)[0]['deploy']; print(d['status'], (d.get('commit') or {}).get('id','')[:7])")
  echo "  $STATUS $SHA"
  case "$STATUS" in
    live) [ "$SHA" = "$HEAD_SHA" ] && { echo "  live on $SHA"; exit 0; }
          echo "  !! live on $SHA, expected $HEAD_SHA"; exit 1;;
    build_failed|update_failed|canceled) echo "  deploy $STATUS"; exit 1;;
  esac
  sleep 20
done
echo "  timed out waiting"; exit 1
