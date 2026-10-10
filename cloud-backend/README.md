# Cloud Backend — Control Plane

FastAPI service for Doppel. Distributes masking policies to local backends and
collects audit events from them. **Carries metadata only — never prompt content.**

This is the cloud component that each customer deploys on their own server. The
Doppel admin dashboard talks to this service. Local backends (running on each
employee's device) pull policy updates from here and push audit events to here.

---

## Table of contents

1. [What this service does](#what-this-service-does)
2. [Architecture](#architecture)
3. [API endpoints](#api-endpoints)
4. [Database schema](#database-schema)
5. [Local development](#local-development)
6. [Deploy on a customer's server](#deploy-on-a-customers-server)
7. [Integrating with a new company](#integrating-with-a-new-company)
8. [Configuration](#configuration)
9. [Security model](#security-model)
10. [Data retention](#data-retention)

---

## What this service does

Three responsibilities, nothing else:

1. **Policy distribution** — store per-org `entity_type → action` rules.
   Local backends pull these on startup and re-pull on policy updates.
2. **Audit collection** — receive metadata-only events from local backends
   (counts, types, timing). Never prompt content.
3. **Dashboard API** — serve aggregated stats and event lists to the admin
   console.

This service does **not**:
- See, store, or transmit prompt content (masked or unmasked)
- Run PII detection itself — that happens on the local backend
- Send anything to OpenAI, Google, or any other AI provider
- Need internet egress in production (it only listens on a port)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Employee device                                                │
│                                                                 │
│  Browser  ──►  Chrome extension  ──►  Local backend             │
│                                          (port 8765)             │
│                                          - Detects PII           │
│                                          - Masks values          │
│                                          - Residual scan         │
│                                                  │               │
└──────────────────────────────────────────────────┼───────────────┘
                                                   │
                                       (metadata only:
                                        entity types,
                                        counts, timing)
                                                   │
                                                   ▼
┌────────────────────────────────────────────────────────────────┐
│  Customer's server (this repo)                                  │
│                                                                 │
│  ┌──────────────────────┐         ┌─────────────────────┐       │
│  │   Cloud backend       │ ◄────── │  Admin dashboard    │       │
│  │   (FastAPI, port 8000)│         │  (port 5173 dev /   │       │
│  │                       │ ──────► │   any static host   │       │
│  │   - SQLite DB         │         │   for prod)         │       │
│  │   - /api/policies     │         └─────────────────────┘       │
│  │   - /api/audit        │                                       │
│  │   - /health           │                                       │
│  └──────────────────────┘                                       │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

The cloud backend runs entirely on the customer's infrastructure. Doppel
(the vendor) never sees any of it.

---

## API endpoints

All endpoints return JSON. Three credentials, each with the least power it needs; missing/invalid
ones get `401`:

| Credential | Header | Can do |
|---|---|---|
| Admin key | `X-API-Key` (or `Authorization: Bearer`) | everything: policies, audit read/delete, endpoint list/deactivate, admin log, rotate enrollment key |
| Enrollment key | `X-Enroll-Key` | `POST /api/endpoints/enroll` only (registers a device, returns its token) |
| Device token | `X-Endpoint-Token` | heartbeat, write audit events *as itself*, read `/api/policies/export` |

Audit deletions and every admin write (`policy.*`, `audit.delete`, endpoint deactivation, key rotation)
are recorded in the admin log (`GET /api/admin/log`).
The organization is derived from the key, so one org can never see or change another's
data. Only a SHA-256 digest of each key is stored. Only `/health` and `/` are public.

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check — `{"status":"ok","service":"cloud-control-plane","version":"1.0.0"}` |
| `GET` | `/` | Redirects to `/health` for convenience |

### Policies — `app/api/policy.py`

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/api/policies?org_id=1` | — | List all policies for an org |
| `GET` | `/api/policies/{entity_type}?org_id=1` | — | Get the policy for a specific entity type |
| `POST` | `/api/policies` | `{org_id?, entity_type, action}` | Create a new policy (409 if exists) |
| `PUT` | `/api/policies/{id}` | `{action?, entity_type?}` | Update a policy (bumps `version`) |
| `DELETE` | `/api/policies/{id}` | — | Delete a non-default policy |
| `GET` | `/api/policies/export?org_id=1` | — | All policies as `{entity_type: action}` JSON (for local backend pull) |

`action` is one of: `"faker"` (replace with realistic stand-in), `"redact"`
(replace with `[[REDACTED]]`), `"keep"` (leave untouched).

### Audit — `app/api/audit.py`

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/api/audit?org_id=1&event_type=&limit=` | — | List events, optionally filtered by type |
| `GET` | `/api/audit/stats?hours=24&org_id=1` | — | Aggregated stats for the dashboard |
| `POST` | `/api/audit` | `{org_id, event_type, entity_types, entity_count, latency_ms, conversation_id}` | Submit a metadata-only event |
| `DELETE` | `/api/audit/{id}` | — | Delete an event (GDPR right to erasure) |

`event_type` is one of: `"mask"`, `"detect"`, `"file"`, `"fail_closed"`.

`entity_types` is a JSON object like `{"PERSON": 3, "EMAIL": 2}` — counts only,
**never** the actual values.

---

## Database schema

SQLite by default (file: `cloud_backend.db`, created on first boot). Four
tables, all owned by this service:

### `organizations`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | Auto-increment |
| `name` | string(255) | Unique |
| `api_key` | string(64) | Unique, indexed — authenticates local backends |
| `is_active` | bool | Default `true` |
| `created_at` | datetime | Auto |

### `policies`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | Auto-increment |
| `org_id` | int FK | → `organizations.id` |
| `entity_type` | string(64) | e.g. `"PERSON"`, `"CREDIT_CARD"`, `"JWT"` |
| `action` | string(32) | `"faker"`, `"redact"`, or `"keep"` |
| `is_default` | bool | Seeded by `seed_defaults()` — locked from delete |
| `version` | int | Bumped on each `PUT` |
| `created_at` / `updated_at` | datetime | Auto |

### `endpoints`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | Auto-increment |
| `org_id` | int FK | → `organizations.id` |
| `hostname` | string(255) | Machine hostname of the local backend |
| `token_hash` | string(64) | SHA-256 of the device token (the token itself is shown once, at enrollment) |
| `last_seen` | datetime | Heartbeat timestamp |
| `version` | string(32) | Local backend version |
| `is_active` | bool | Default `true` |
| `created_at` | datetime | Auto |

### `audit_events`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | Auto-increment |
| `org_id` | int FK | → `organizations.id` |
| `endpoint_id` | int FK | → `endpoints.id` (nullable) |
| `event_type` | string(64) | `"mask"`, `"detect"`, `"file"`, `"fail_closed"` |
| `entity_types` | JSON | `{"PERSON": 3, "EMAIL": 2}` — counts only |
| `entity_count` | int | Total entities touched |
| `latency_ms` | int | P95 latency for the operation |
| `conversation_id` | string(64) | For grouping related events |
| `timestamp` | datetime | Indexed — when the event was received |

**No prompt content is ever stored.** No `text`, no `prompt`, no `response`,
no `value` columns anywhere. The schema is auditable by anyone — the
guarantee is structural.

---

## Local development

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```powershell
# From the cloud-backend/ folder
uv venv
uv pip install -r requirements.txt

# Or with pyproject.toml (modern uv):
uv sync

# Run the server
uv run uvicorn app.main:app --port 8000 --reload
```

Open `http://localhost:8000/health` to verify.

The SQLite DB file (`cloud_backend.db`) is auto-created on first boot, the
default organization (`Acme`, `org_id=1`) is seeded, and 15 default policies
are inserted:

| Entity types | Default action |
|---|---|
| `PERSON`, `EMAIL`, `PHONE_NUMBER`, `ORGANIZATION`, `ADDRESS`, `USERNAME` | `faker` |
| `CREDIT_CARD`, `API_KEY`, `JWT`, `PEM_BLOCK`, `IBAN`, `SSN` | `redact` |
| `URL`, `IPV4`, `IPV6` | `keep` |

---

## Deploy on a customer's server

This is the deployment recipe for any company that wants to run Doppel on
their own infrastructure. The cloud backend is intentionally self-contained:
no external services, no SaaS dependencies, no telemetry back to the vendor.

### Step 1 — Provision a server

Minimum: any Linux VM with 1 vCPU + 1 GB RAM + 10 GB disk. Tested on
Ubuntu 22.04 / Debian 12 / Amazon Linux 2023.

For high-volume deployments (10k+ audit events per minute), upgrade to 2 vCPU
+ 4 GB RAM and switch from SQLite to Postgres (see
[Configuration](#configuration)).

### Step 2 — Install dependencies on the server

```bash
# Install uv (one-line installer)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repo
git clone https://github.com/FarahElshenawi/AI_Privacy_Gateway.git
cd AI_Privacy_Gateway/cloud-backend
```

### Step 3 — Set up the Python environment

```bash
# Create a virtualenv + install deps + generate lockfile
uv sync
```

### Step 4 — Configure for production

Create a `.env` file at `cloud-backend/.env`:

```bash
# Use Postgres in production (SQLite is fine for small deployments)
DB_URL=postgresql+psycopg://doppel:doppel@localhost:5432/doppel

# Restrict CORS to the dashboard's domain only
CORS_ORIGINS=https://doppel-dashboard.acme.io,https://doppel-cloud.internal.acme.io

# Optional: bind to a specific interface (default 0.0.0.0 = all)
HOST=0.0.0.0
PORT=8000
```

### Step 5 — Run it behind a reverse proxy (recommended)

Use nginx or Caddy as a TLS-terminating reverse proxy in front of uvicorn:

**nginx example:**

```nginx
server {
  listen 443 ssl http2;
  server_name doppel-cloud.acme.io;

  ssl_certificate /etc/letsencrypt/live/doppel-cloud.acme.io/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/doppel-cloud.acme.io/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

**Run uvicorn on localhost only (behind the proxy):**

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

Use `--workers 4` (or `--workers $(nproc)`) for production. The default SQLite
DB supports concurrent reads; for high-volume writes, switch to Postgres.

### Step 6 — Run as a systemd service

Create `/etc/systemd/system/doppel-cloud.service`:

```ini
[Unit]
Description=Doppel Cloud Control Plane
After=network.target postgresql.service

[Service]
Type=simple
User=doppel
WorkingDirectory=/opt/doppel/cloud-backend
EnvironmentFile=/opt/doppel/cloud-backend/.env
ExecStart=/opt/doppel/cloud-backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now doppel-cloud
sudo systemctl status doppel-cloud
```

### Step 7 — Verify

```bash
curl https://doppel-cloud.acme.io/health
# → {"status":"ok","service":"cloud-control-plane","version":"1.0.0"}

curl https://doppel-cloud.acme.io/api/policies
# → 15 default policies
```

---

## Integrating with a new company

When a new customer wants to deploy Doppel, here's the integration playbook.
The whole thing takes 30-60 minutes.

### 1. Provision their org record

Once the cloud backend is running on their server, create their organization:

```bash
python -m app.admin create-org "NewCompany Inc."
# organization id=2 name='NewCompany Inc.'
# API key (shown once): <key>
```

The key is shown once; only its hash is stored. For a single-org deployment, set
`CLOUD_ADMIN_API_KEY` (>= 24 chars) before first boot — or leave it unset and a key is
generated and printed once in the server log. Setting it later rotates the default org's key.

### 2. Distribute their enrollment key

Local backends get the **enrollment key**, never the admin key. It can only register a device;
each device then receives its own token, which is revocable (deactivate it in the dashboard).
Set `CLOUD_ENROLL_KEY` (>= 24 chars, different from the admin key) before first boot, or let it be
generated and printed once; rotate with `POST /api/admin/rotate-enroll-key`.
Hand it over via a secure channel — never commit it, never email it.

The customer adds it to each employee's local backend `.env`:

```
CLOUD_ENROLL_KEY=<enrollment key>
CLOUD_URL=https://doppel-cloud.NEWCOMPANY.com
```

Their local backends will use it to authenticate when pulling policies and
pushing audit events. The cloud backend enforces this key on every API call.

### 3. Configure their policies

By default, the 15 seeded policies (PERSON→faker, CREDIT_CARD→redact, etc.)
are inherited. If the customer wants different rules:

- **Easy way:** Use the dashboard at `https://doppel-dashboard.NEWCOMPANY.com/policies`
  and toggle the chips inline.
- **Reproducible way:** Use the API directly:

```bash
# Override PERSON → redact (instead of faker) for NewCompany
curl -X PUT https://doppel-cloud.NEWCOMPANY.com/api/policies/1 \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your key>" \
  -d '{"action": "redact"}'

# Add a custom policy for a customer-specific entity type
curl -X POST https://doppel-cloud.NEWCOMPANY.com/api/policies \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your key>" \
  -d '{"entity_type": "INTERNAL_EMPLOYEE_ID", "action": "redact"}'
```

### 4. Point their local backends at this cloud

Each employee's `local-backend/.env` should point at the customer's cloud
backend URL (not Doppel's, not localhost):

```
CLOUD_URL=https://doppel-cloud.NEWCOMPANY.com
CLOUD_ENROLL_KEY=<enrollment key>
PULL_POLICY_INTERVAL=300    # seconds between policy pulls
PUSH_AUDIT_INTERVAL=10      # seconds between audit pushes
```

On startup, the local backend pulls the policy set and begins pushing audit
events as the user sends prompts to AI tools.

### 5. Point their dashboard at this cloud

The dashboard runs as a separate static site (build with `npm run build`,
serve `dist/` from any static host). It reads `VITE_CLOUD_BACKEND_URL` at
build time. For each customer:

```bash
# In their dashboard/ folder
echo "VITE_CLOUD_BACKEND_URL=https://doppel-cloud.NEWCOMPANY.com" > .env
npm run build
# Deploy dist/ to https://doppel-dashboard.NEWCOMPANY.com
```

### 6. Verify end-to-end

Once a few employees are running the local backend + extension, the dashboard
at `https://doppel-dashboard.NEWCOMPANY.com` should start showing:

- **Overview**: KPI cards incrementing, masked-vs-held donut filling in,
  entity-type bar chart populated
- **Audit log**: streaming rows appearing every few seconds
- **Policies**: their custom rules (if any) appear alongside the defaults

If the dashboard shows "Cloud backend offline" or zero events after 10
minutes, check the [Troubleshooting](#troubleshooting) section below.

### Integration checklist (for each new customer)

- [ ] Cloud backend deployed on their server (systemd + nginx + TLS)
- [ ] `organizations` row created with their name + generated `api_key`
- [ ] `api_key` delivered to their IT/security team via secure channel
- [ ] Their policies reviewed (defaults OK? any overrides needed?)
- [ ] Dashboard built with `VITE_CLOUD_BACKEND_URL` pointed at their cloud
- [ ] Dashboard deployed to their internal domain
- [ ] Local backends installed on employee devices with the correct `CLOUD_URL`
- [ ] Chrome extension loaded on employee browsers
- [ ] Smoke test: a test prompt with PII shows up in the audit log within 60s

---

## Configuration

The cloud backend reads configuration from environment variables (or a `.env`
file in the working directory). All are optional.

| Variable | Default | Description |
|---|---|---|
| `DB_URL` | `sqlite:///./cloud_backend.db` | SQLAlchemy database URL. Use `postgresql+psycopg://user:pass@host/db` for Postgres. |
| `CORS_ORIGINS` | `*` | Comma-separated list of allowed origins for the dashboard. Restrict in production. |
| `HOST` | `0.0.0.0` | Bind address. Use `127.0.0.1` behind a reverse proxy. |
| `PORT` | `8000` | Listen port. |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warning` / `error` |

---

## Security model

### What this service stores

- Org names, API keys, endpoint enrollment tokens
- Per-org policy rules (entity_type → action)
- Audit event metadata: types, counts, latency, timestamps, conversation IDs

### What this service does NOT store

- Prompt content (the actual text the user typed)
- Response content (the AI's reply)
- Real PII values (names, emails, cards, etc.)
- Masked values
- The (real → fake) mapping vault
- Anything from the user's file uploads (PDFs, Word, Excel)

The mapping vault lives on the employee's device in the local backend — it
never leaves that machine. The cloud backend sees only the metadata envelope
("we masked 2 PERSONs, 1 EMAIL, 1 CREDIT_CARD in conversation abc123, took
23ms").

### Network exposure

In production, only the dashboard and the local backends should be able to
reach this service. Restrict via:

1. **CORS** — `CORS_ORIGINS=https://doppel-dashboard.acme.io` (whitelist the
   dashboard's domain)
2. **Firewall** — only allow traffic from the dashboard's IP and the VPN
   range used by employee devices
3. **API key auth** — add middleware to require a valid `X-Api-Key` header
   on `/api/policies/*` and `/api/audit/*` routes (not implemented in the
   current code — add before production)

### TLS

Always terminate TLS at the reverse proxy (nginx, Caddy, AWS ALB). The cloud
backend itself speaks HTTP — it should never be exposed directly to the
internet without TLS.

---

## Data retention

Audit events accumulate forever unless explicitly deleted. The dashboard
exposes a per-event delete button that calls `DELETE /api/audit/{id}` — useful
for GDPR right-to-erasure requests.

For automatic retention, add a daily cron job:

```bash
# Delete audit events older than 90 days
0 3 * * * sqlite3 /opt/doppel/cloud-backend/cloud_backend.db \
  "DELETE FROM audit_events WHERE timestamp < datetime('now', '-90 days')"
```

For Postgres:

```sql
DELETE FROM audit_events WHERE timestamp < NOW() - INTERVAL '90 days';
```

For high-volume deployments, partition the `audit_events` table by timestamp
and drop old partitions.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'app.db'`

The `app/` folder isn't a proper Python package. Add empty `__init__.py`
files:

```powershell
New-Item app\__init__.py -ItemType File -Force
New-Item app\api\__init__.py -ItemType File -Force
```

### `ERR_ADDRESS_INVALID` when visiting `http://0.0.0.0:8000/` in a browser

Windows doesn't allow connecting to `0.0.0.0`. Use `http://localhost:8000/`
or `http://127.0.0.1:8000/` instead. The `--host 0.0.0.0` flag is for the bind
address (which interfaces to listen on), not the URL to visit.

### `{"detail":"Not Found"}` at the root URL

Visit `/health` directly. The root path redirects to `/health` — if your
client doesn't follow redirects (like `curl` without `-L`), you'll see the
404 from the redirect response.

### Dashboard shows "Cloud backend offline" but `/health` works

Check `CORS_ORIGINS` — the dashboard's URL must be in the allowed list. For
local dev, leave it as `*`. For production, list the dashboard's domain
explicitly.

### Policies list is empty

The seed defaults didn't run. Restart the server — `seed_defaults()` runs
on the `startup` event. If you're using a fresh DB file, it should populate
15 rows automatically.

---

## License

See the parent repo's LICENSE.


## Tenant config

`GET /api/tenant-config` (admin key or device token) and `PUT /api/tenant-config` (admin key only) hold the
org's `deny_terms` (2-100 chars each, max 500) and `tenant_domains` (hostnames, max 200). Local backends pull
them with the policies. Deny terms are never written to the admin log; it records only the counts.
