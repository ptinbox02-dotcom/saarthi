#!/bin/bash
# Runs the in-page layout assertions against the real app in headless Chrome, at a
# spread of viewports and content densities, and captures a screenshot of each.
#
#   bash classroom/tests/visual.sh [port]
#
# Chrome is given its own profile per case: a shared --user-data-dir makes the second
# headless launch block on the first one's lock. It also does not exit after writing a
# screenshot, so each case is backgrounded and reaped once its PNG appears.
set -u
PORT="${1:-8756}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHOTS="$ROOT/shots"; TMP="${TMPDIR:-/tmp}/saarthi-visual"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
mkdir -p "$SHOTS" "$TMP"; rm -rf "$TMP"/chrome-* "$SHOTS"/*.png

CASES=(
  "00-landing|/?check=1|1440|900"
  "00-landing-narrow|/?check=1|1120|780"
  "01-rich|/?demo=/_fx_rich.json|1440|900"
  "02-sparse|/?demo=/_fx_sparse.json&q=s+aur+p+orbital+mein+kya+farq+hai|1440|900"
  "03-waiting|/?demo=/_fx_rich.json&state=waiting|1440|900"
  "04-transcript|/?demo=/_fx_wall.json&notes=1|1440|900"
  "05-narrow|/?demo=/_fx_rich.json|1120|780"
  "06-wide|/?demo=/_fx_rich.json|1920|1080"
  "07-short|/?demo=/_fx_wall.json|1440|700"
  "08-teacher|/?demo=/_fx_rich.json&teacher=1|1440|900"
  "09-offline|/?demo=/_fx_sparse.json&offline=1|1440|900"
  "10-interactive|/?demo=/_fx_rich.json&draw=1|1440|900"
  "11-selection|/?demo=/_fx_rich.json&select=1|1440|900"
  "12-animated|/?demo=/_fx_rich.json&animate=1|1440|900"
)

run () {  # name url w h  → prints the assertion result, leaves a PNG behind
  local name="$1" url="$2" w="$3" h="$4"
  local dom="$TMP/$name.html"
  "$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --force-device-scale-factor=2 --virtual-time-budget=20000 --window-size="$w,$h" \
    --user-data-dir="$TMP/chrome-$name" --screenshot="$SHOTS/$name.png" \
    --dump-dom "http://localhost:$PORT$url" >"$dom" 2>/dev/null &
  local pid=$!
  for _ in $(seq 1 40); do [ -s "$dom" ] && grep -q 'data-ready' "$dom" && break; sleep 0.5; done
  sleep 1; kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  python3 - "$name" "$dom" "$SHOTS/$name.png" <<'PY'
import html, json, os, re, sys
name, dom, png = sys.argv[1:4]
src = open(dom, encoding="utf-8", errors="replace").read() if os.path.exists(dom) else ""
m = re.search(r'data-checks="([^"]*)"', src)
if not m:
    print(f"  {name:<14} ERROR  page never reported (no data-checks)"); sys.exit(1)
r = json.loads(html.unescape(m.group(1)))
# a fixture that exercises behaviour reports its own failures alongside the layout ones
mi = re.search(r'data-interact="([^"]*)"', src)
if mi:
    r["fail"] = list(r["fail"]) + json.loads(html.unescape(mi.group(1)))["fail"]
shot = "shot" if os.path.exists(png) else "NO SHOT"
if r["fail"]:
    print(f"  {name:<14} FAIL   board={r['board']} ops={r['ops']} [{shot}]")
    for f in r["fail"]: print(f"                   - {f}")
    sys.exit(1)
print(f"  {name:<14} pass   board={r['board']} ops={r['ops']} font={r['font']} [{shot}]")
PY
}

echo "visual layout checks (port $PORT)"
rc=0
for c in "${CASES[@]}"; do IFS='|' read -r n u w h <<< "$c"; run "$n" "$u" "$w" "$h" || rc=1; done
echo
[ $rc -eq 0 ] && echo "all viewports pass · screenshots in classroom/shots/" \
              || echo "FAILURES above · screenshots in classroom/shots/"
exit $rc
