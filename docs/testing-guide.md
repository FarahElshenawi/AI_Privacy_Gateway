# Testing Guide — Doppel AI Privacy Gateway

How to verify the whole stack, with focus on the admin dashboard
(`dashboard/`). Covers the automated suite and a manual end-to-end
walkthrough of all four dashboard screens.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [One-command setup](#one-command-setup)
3. [Part A — Automated test suite](#part-a--automated-test-suite)
4. [Part B — Manual end-to-end dashboard test](#part-b--manual-end-to-end-dashboard-test)
   - [Start the services](#start-the-services)
   - [Get your API key](#get-your-api-key)
   - [API smoke tests (curl)](#api-smoke-tests-curl)
   - [Screen-by-screen checklist](#screen-by-screen-checklist)
   - [Generate test events](#generate-test-events)
   - [Failure modes to verify](#failure-modes-to-verify)
5. [Teardown](#teardown)
6. [Gotchas](#gotchas)

---

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (or pip)
- Node.js 18+ (v22 recommended)
- Ports `8000` (cloud backend) and `5173` (dashboard dev server) free

## One-command setup

```bash
cd AI_Privacy_Gateway
./scripts/setup_dev_env.sh
```

This creates the Python venvs for both backends and runs `npm install`
in `dashboard/` and `extension/`.

> ⚠️ **Important:** if `dashboard/node_modules` was installed
> incompletely (e.g. copied from elsewhere), the dev server starts but
> pages render broken — the console shows errors like `Cannot find
> package '@tailwindcss/postcss'` or `Failed to resolve import
> "tailwind-merge"`. Fix: `cd dashboard && rm -rf node_modules &&
> npm install`.

---

## Part A — Automated test suite

The canonical runner is:

```bash
./scripts/run_all_tests.sh
```

It runs, in order:

| Step | Command | What it covers |
|---|---|---|
| 1 | `local-backend`: `.venv/bin/pytest` | PII detection, masking, vault, residual scan |
| 2 | `cloud-backend`: `.venv/bin/pytest` | Auth, org isolation, policy CRUD, seeding, tenant config, validation (`cloud-backend/tests/`) |
| 3 | `extension`: `npm test` | Chrome extension unit tests |
| 4 | `dashboard`: `npx tsc --noEmit && npm run build` | Type-check + production build |

Dashboard-only equivalent:

```bash
cd dashboard
npm run build          # production build → dist/
```

Backend-only equivalent:

```bash
cd cloud-backend && .venv/bin/pytest -v
```

---

## Part B — Manual end-to-end dashboard test

### Start the services

**Terminal 1 — cloud backend:**

```bash
cd cloud-backend
# Optional: pin the admin key instead of using the random one printed at boot
export CLOUD_ADMIN_API_KEY="test-admin-key-1234567890abcd"
export CLOUD_ENROLL_KEY="test-enroll-key-1234567890abcd"
.venv/bin/uvicorn app.main:app --port 8000
```

On first boot the backend:

- creates `cloud_backend.db` (SQLite, **relative to the current working
  directory** — run from `cloud-backend/`),
- seeds the default organization ("Default Organization", `org_id=1`),
- seeds ~34 default policies (PERSON→faker, CREDIT_CARD→redact,
  URL/IPV4/IPV6→keep, …),
- prints the admin API key **once** to the terminal (only its SHA-256
  hash is stored). Set `CLOUD_ADMIN_API_KEY` to skip the random key.

**Terminal 2 — dashboard:**

```bash
cd dashboard
cp .env.example .env   # VITE_CLOUD_BACKEND_URL=http://localhost:8000
npm run dev            # → http://localhost:5173
```

The Vite dev server proxies `/api/*` to `http://localhost:8000`, so the
dashboard works with or without the env var in dev.

Verify both are up:

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"cloud-control-plane","version":"1.0.0"}
curl -o /dev/null -w "%{http_code}\n" http://localhost:5173
# 200
```

### Get your API key

The dashboard needs the org's admin key, entered in **Settings** →
"Per-org API key" (kept in `sessionStorage`, cleared when the tab
closes). Either:

- use the key you set via `CLOUD_ADMIN_API_KEY` above, or
- copy the key printed once at backend boot, or
- create a fresh org: `python -m app.admin create-org "Acme Inc."`
  (run from `cloud-backend/`) — prints admin + enrollment keys once.

### API smoke tests (curl)

```bash
KEY="test-admin-key-1234567890abcd"

# 1. Health (no auth needed)
curl -s http://localhost:8000/health

# 2. Policies list — expect ~34 seeded policies
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/policies?org_id=1"

# 3. Auth is enforced — expect 401 without the key
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/policies

# 4. Submit a metadata-only audit event — expect 201 + stored JSON
curl -s -X POST http://localhost:8000/api/audit \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"event_type":"mask","entity_types":{"PERSON":2,"EMAIL":1},
       "entity_count":3,"latency_ms":23}'

# 5. Aggregated stats — expect the event reflected:
#    total_events 1, total_entities_masked 3, avg_latency_ms 23,
#    entity_type_breakdown {"PERSON":2,"EMAIL":1}
curl -s -H "X-API-Key: $KEY" \
  "http://localhost:8000/api/audit/stats?hours=24&org_id=1"

# 6. Event list — expect the event from step 4
curl -s -H "X-API-Key: $KEY" \
  "http://localhost:8000/api/audit?limit=10&org_id=1"

# 7. Policy export (what local backends pull) — expect {entity_type: action} map
curl -s -H "X-API-Key: $KEY" "http://localhost:8000/api/policies/export?org_id=1"

# 8. Through the dashboard's dev-server proxy — same answers via :5173
curl -s -H "X-API-Key: $KEY" \
  "http://localhost:5173/api/audit/stats?hours=24&org_id=1"

# 9. GDPR erasure — delete the test event, expect 200/204
curl -s -X DELETE -H "X-API-Key: $KEY" \
  "http://localhost:8000/api/audit/2"
```

### Screen-by-screen checklist

Open http://localhost:5173, go to **Settings**, paste the API key.

**Settings (`/settings`)**

- [ ] Org card shows the organization name and org ID (1)
- [ ] Cloud backend URL shown, health status green (polls `GET /health` every 10s)
- [ ] Exported policy snapshot renders the live JSON from `/api/policies/export`

**Overview (`/`)** — polls stats + recent events every 5s

- [ ] 4 KPI cards match the curl stats output (events, entities masked, fail-closed, avg latency)
- [ ] Activity line chart (mask events per hour, last 12h)
- [ ] Outcome split donut (masked / fail-closed / other)
- [ ] Top entity-types bar chart (top 8 by masked count)
- [ ] Recent events feed shows the test event within ~5s

**Audit log (`/audit`)** — polls every 2s when Live is on

- [ ] Live/Paused toggle works (pausing stops the 2s polling)
- [ ] Filter chips work: All / Masked / Detected / File upload / Fail-closed
  (`?event_type=mask` etc.)
- [ ] Rows show time, type, entity chips (e.g. `PERSON ×2`), relative time, latency
- [ ] × deletes the row (`DELETE /api/audit/{id}`) and it disappears
- [ ] Footer count: "X of last 50 events shown"

**Policies (`/policies`)** — loads once, no polling

- [ ] Table lists entity types with action chips (faker / redact / keep)
- [ ] Clicking a chip PUTs the change; the version number bumps
- [ ] Default policies show a lock and cannot be deleted
- [ ] "New policy" → inline form → POST creates a row; × deletes non-default rows
- [ ] Changes also appear in the exported policy snapshot on Settings

### Generate test events

To see charts move, fire a mix of event types:

```bash
KEY="test-admin-key-1234567890abcd"
for i in 1 2 3; do
  curl -s -X POST http://localhost:8000/api/audit \
    -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
    -d "{\"event_type\":\"mask\",\"entity_types\":{\"PERSON\":$i,\"EMAIL\":1},\"entity_count\":$((i+1)),\"latency_ms\":$((20+i*5))}"
done
curl -s -X POST http://localhost:8000/api/audit \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"event_type":"file","entity_types":{"CREDIT_CARD":1},"entity_count":1,"latency_ms":120}'
curl -s -X POST http://localhost:8000/api/audit \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"event_type":"fail_closed","entity_types":{},"entity_count":0,"latency_ms":null}'
```

Each event appears in the dashboard within ~2 seconds (Live on).

### Failure modes to verify

| Scenario | Expected result |
|---|---|
| Backend stopped | Top-bar status dot goes muted; screens show "Can't reach the cloud backend" |
| Wrong API key in Settings | 401 → "API key rejected — check Settings." |
| No API key entered | "Enter your API key in Settings." |
| Events older than 24h | Absent from KPI cards (stats use `?hours=24`) but still listed on the Audit page |
| Dashboard origin not in backend `CORS_ORIGINS` | Browser console CORS error (dev avoids this via the Vite proxy) |

---

## Teardown

```bash
# In each terminal: Ctrl+C (uvicorn, vite)
# Or from anywhere:
pkill -f "uvicorn app.main:app"; pkill -f "node.*vite"
```

Reset test data by deleting the SQLite DB (created relative to the
backend's working directory) and restarting the backend:

```bash
rm cloud-backend/cloud_backend.db
```

---

## Gotchas

- **API keys are one-time.** Only SHA-256 digests are stored; if you
  lose the key, rotate it with `CLOUD_ADMIN_API_KEY` (restart) or
  create a new org via `python -m app.admin create-org`.
- **Two credentials, different powers:** the admin key (`X-API-Key`,
  full dashboard access) vs. the enrollment key (`X-Enroll-Key`,
  only device registration — this is what goes on employee machines).
- **The dashboard stores nothing** — no localStorage, no cache. Every
  number comes from a live `GET`; the API key lives in `sessionStorage`.
- **`VITE_CLOUD_BACKEND_URL` is baked in at build time** for
  production (`npm run build`); change it and rebuild to retarget.
- **Org isolation is server-side:** the org is derived from the key,
  so one org can never read or modify another's data.
