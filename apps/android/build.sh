#!/usr/bin/env bash
# Build the demo APK.
#
#   bash android-app/build.sh          -> build/Saarthi-demo.apk
#
# The UI ships inside the APK; the lessons, the media and the tutor socket go over the
# network to whichever server is entered on the app's first screen. That address is
# settable at runtime on purpose — a tunnel hands out a new one every restart, and
# nobody should need a rebuild to move a demo.
set -euo pipefail
cd "$(dirname "$0")"

export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export PATH="$JAVA_HOME/bin:$PATH"

[ -x "$JAVA_HOME/bin/java" ] || { echo "no JDK at $JAVA_HOME (brew install openjdk@21)"; exit 1; }
[ -d "$ANDROID_HOME/platforms" ] || { echo "no Android SDK at $ANDROID_HOME"; exit 1; }

bash sync.sh
echo "sdk.dir=$ANDROID_HOME" > android/local.properties
(cd android && ./gradlew assembleDebug --console=plain -q)

mkdir -p ../../build
cp android/app/build/outputs/apk/debug/app-debug.apk ../../build/Saarthi-demo.apk
printf '\n  build/Saarthi-demo.apk  %s\n' "$(du -h ../../build/Saarthi-demo.apk | cut -f1)"
