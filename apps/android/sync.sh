#!/usr/bin/env bash
# Copy the web client into the Android shell. The UI ships inside the APK; only the
# lesson data, the media and the tutor socket go over the network, to whichever server
# is set on the first screen.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf www && mkdir -p www
cp ../../classroom/web/index.html ../../classroom/web/app.js ../../classroom/web/board.js \
   ../../classroom/web/app.css ../../classroom/web/DESIGN_TOKENS.css www/
npx cap sync android
