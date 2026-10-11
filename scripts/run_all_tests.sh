#!/usr/bin/env bash
set -euo pipefail
(cd local-backend && .venv/bin/pytest)
(cd cloud-backend && .venv/bin/pytest)
(cd extension && npm test)
(cd dashboard && npx tsc --noEmit && npm run build)
