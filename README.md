# AI Privacy Gateway

Endpoint PII masking for public LLM web UIs. Intercepts prompts locally, masks sensitive
data before it reaches the LLM, and reversibly restores real values in the response.

Full architecture and rationale: `docs/architecture.md`. Full 4-week build plan: `docs/development-plan.md`.

## Repo Map

This repo mirrors the system's two planes, plus a shared evaluation package.

| Path              | Plane      | What lives here                                            | Owner  |
|--------------------|-----------|-------------------------------------------------------------|--------|
| `extension/`       | Data      | Chrome extension (Manifest V3), MAIN-world fetch() override | Role 3 |
| `local-backend/`   | Data      | FastAPI local backend: pipeline, Mapping Vault, security     | Role 1 / Role 2 / Role 4 |
| `cloud-backend/`   | Control   | FastAPI cloud backend: policy distribution, audit            | Role 5 |
| `dashboard/`       | Control   | Admin dashboard (React): policy config, verification stats   | Role 2 |
| `eval/`            | Shared    | Hand-built eval set + scoring scripts for the 5 metrics      | Role 1 |
| `docs/`            | Shared    | Architecture, development plan, ADRs                         | All    |
| `infra/`           | Shared    | Deployment configs (VPS, docker-compose)                      | Role 5 |
| `scripts/`         | Shared    | Dev setup, model fetch, test runner                            | All    |

Within `local-backend/app/`, ownership splits further along the pipeline:

| Path                          | Pipeline step                        | Owner  |
|--------------------------------|---------------------------------------|--------|
| `pipeline/detection.py`        | Step 1 — Detection (GLiNER2-PII)      | Role 1 |
| `pipeline/routing.py`          | Step 2 — Deterministic routing table  | Role 4 |
| `pipeline/decision.py`         | Step 3 — SLM decision tier (Qwen)     | Role 4 |
| `pipeline/masking.py`          | Step 4 — Masking (faker/redact/context) | Role 3 |
| `pipeline/residual_scanner.py` | Step 5 — Independent safety net       | Role 1 |
| `vault/`                       | Mapping Vault (state, collision, expiry) | Role 2 |
| `security/`                    | Origin check, per-install token       | Role 2 |

See `CODEOWNERS` for enforced review routing.

## Quickstart

```bash
git clone <repo-url> && cd ai-privacy-gateway
./scripts/setup_dev_env.sh      # installs backend + dashboard + extension deps
./scripts/fetch_models.sh       # pulls GLiNER2-PII / Qwen-RLCD ONNX artifacts (gitignored)
docker compose -f infra/docker-compose.yml up --build
```

Then load `extension/` as an unpacked extension in `chrome://extensions` (Developer Mode).

## Status

Architecture locked (v4). See `docs/adr/` for the decisions behind it, including the
zero-shot-before-fine-tuning gate in Week 1.
