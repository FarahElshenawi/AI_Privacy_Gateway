#!/usr/bin/env bash
# Install the local backend as a per-user launchd agent (macOS). Run from the repo root.
#   MODEL_REVISION=<sha> CLOUD_URL=https://... CLOUD_ENROLL_KEY=... installer/macos/install.sh
set -euo pipefail
: "${MODEL_REVISION:?set MODEL_REVISION to the full commit sha of the Tier 2 model to pin}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${DOPPEL_HOME:-$HOME/Library/Application Support/Doppel}"
mkdir -p "$APP" "$HOME/Library/LaunchAgents"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet -r "$ROOT/local-backend/requirements.txt" huggingface_hub
rm -rf "$APP/app" && mkdir -p "$APP/app" && cp -r "$ROOT/local-backend/app" "$ROOT/local-backend/dlp_core" "$APP/app/"
"$APP/venv/bin/python" "$ROOT/scripts/fetch_model.py" --revision "$MODEL_REVISION" --dest "$APP/model"
PLIST="$HOME/Library/LaunchAgents/com.doppel.backend.plist"
umask 077       # the plist carries the enrollment key
sed "s#@APP@#$APP#g; s#@EXT_IDS@#${DLP_EXTENSION_IDS:-}#; s#@CLOUD_URL@#${CLOUD_URL:-}#; s#@ENROLL@#${CLOUD_ENROLL_KEY:-}#" \
  "$ROOT/installer/macos/com.doppel.backend.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Doppel backend running on 127.0.0.1:8765 (launchctl list | grep doppel)"
