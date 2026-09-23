# Local Backend — Data Plane

FastAPI service on `localhost:8765`. Orchestrates the tiered-hybrid pipeline
(detection -> routing -> decision -> masking -> residual scan -> demasking)
and hosts the Mapping Vault, the system's most sensitive component.

See docs/architecture.md Part 1 for the full pipeline and Vault spec.

## Layout

- `app/api/` — /detect, /mask, /demask endpoints (Role 2)
- `app/pipeline/` — the 5 pipeline steps (Role 1 / Role 4 / Role 3, split per README at repo root)
- `app/vault/` — Mapping Vault: bijective, collision-checked, 24h expiry (Role 2)
- `app/security/` — origin check + per-install token, fail-closed enforcement (Role 2)
- `models/` — ONNX artifacts, gitignored, fetched via `scripts/fetch_models.sh`
