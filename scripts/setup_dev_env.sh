#!/usr/bin/env bash
set -euo pipefail
echo "Setting up local-backend..."
(cd local-backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

echo "Setting up cloud-backend..."
(cd cloud-backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)

echo "Setting up dashboard..."
(cd dashboard && npm install)

echo "Setting up extension..."
(cd extension && npm install)

echo "Done. Copy .env.example to .env and adjust as needed."
