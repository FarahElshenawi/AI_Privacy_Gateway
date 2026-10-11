#!/usr/bin/env bash
# Install the local backend as a per-user systemd service (Linux). Run from the repo root.
#   MODEL_REVISION=<sha> CLOUD_URL=https://... CLOUD_ENROLL_KEY=... installer/linux/install.sh
set -euo pipefail
: "${MODEL_REVISION:?set MODEL_REVISION to the full commit sha of the Tier 2 model to pin}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${DOPPEL_HOME:-$HOME/.local/share/doppel}"
mkdir -p "$APP" "$HOME/.config/doppel" "$HOME/.config/systemd/user"

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet -r "$ROOT/local-backend/requirements.txt" huggingface_hub
rm -rf "$APP/app" && mkdir -p "$APP/app" && cp -r "$ROOT/local-backend/app" "$ROOT/local-backend/dlp_core" "$APP/app/"
find "$APP/app" \( -name 'test_*.py' -o -name tests -o -path '*/dlp_core/eval' \) -prune -exec rm -rf {} +   # runtime only
"$APP/venv/bin/python" "$ROOT/scripts/fetch_model.py" --revision "$MODEL_REVISION" --dest "$APP/model"

ENVF="$HOME/.config/doppel/env"
umask 077
cat > "$ENVF" <<EOF
DLP_TIER2_MODEL=$APP/model
DLP_EXTENSION_IDS=${DLP_EXTENSION_IDS:-}
CLOUD_URL=${CLOUD_URL:-}
CLOUD_ENROLL_KEY=${CLOUD_ENROLL_KEY:-}
HF_HUB_OFFLINE=1
EOF
sed "s#@APP@#$APP#g; s#@ENV@#$ENVF#g" "$ROOT/installer/linux/doppel.service" > "$HOME/.config/systemd/user/doppel.service"
systemctl --user daemon-reload
systemctl --user enable --now doppel.service
echo "Doppel backend running on 127.0.0.1:8765 (systemctl --user status doppel)"
