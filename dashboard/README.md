# Doppel — Admin Dashboard

The customer-facing admin console for Doppel. A Vite + React + TypeScript
SPA that talks to the Doppel cloud backend over a small, typed API.

This README is the **specification** of what the dashboard tracks and logs —
the data contract between the dashboard, the cloud backend, and the local
backends. Read it before deploying in any environment where compliance
matters.

---

## Table of contents

1. [What this dashboard is](#what-this-dashboard-is)
2. [What we track](#what-we-track)
3. [What we log](#what-we-log)
4. [What we do NOT track or log](#what-we-do-not-track-or-log)
5. [Per-screen breakdown](#per-screen-breakdown)
6. [Tech stack](#tech-stack)
7. [Setup](#setup)
8. [Configuration](#configuration)
9. [Data sources](#data-sources)
10. [Compliance notes](#compliance-notes)

---

## What this dashboard is

A read-and-control surface for one Doppel organization. It shows what the
local backends in your org have been doing (audit view) and lets you
configure the rules they follow (policy view).

The dashboard itself **stores nothing**. It's a thin client over the cloud
backend. Every byte of data shown comes from a `GET` to the cloud backend,
every change you make is a `PUT`/`POST`/`DELETE` to it. There's no local
storage, no IndexedDB, no offline cache.

---

## What we track

The dashboard displays these metrics, all derived from audit events in the
cloud backend. **None of them contain prompt content or PII values.**

### KPI cards (Overview page)

| Metric | Source endpoint | Field | What it means |
|--------|----------------|-------|---------------|
| Total events | `GET /api/audit/stats?hours=24` | `total_events` | Count of audit events in the last 24h, across all local backends |
| Entities masked | same | `total_entities_masked` | Sum of `entity_count` across all `mask` events in the window |
| Fail-closed events | same | `fail_closed_events` | Count of events where the local backend blocked the request (residual leak, backend down, exception) |
| Avg latency | same | `avg_latency_ms` | Mean `latency_ms` across `mask` events in the window — proxy for end-to-end overhead added by the gateway |

### Charts (Overview page)

| Chart | Source | What it shows |
|-------|--------|---------------|
| Activity line chart | `GET /api/audit?limit=50` (bucketed by hour client-side) | Number of `mask` events per hour, last 12 hours |
| Outcome split donut | `GET /api/audit/stats` → `by_event_type` + `fail_closed_events` | Masked vs fail-closed vs other, by count |
| Entity types bar chart | `GET /api/audit/stats` → `entity_type_breakdown` | Top 8 entity types (PERSON, EMAIL, etc.) by total masked count |

### Live indicators

| Indicator | Source | Refresh | What it shows |
|-----------|--------|---------|---------------|
| Top bar status | `GET /health` | every 10s | Green dot if cloud backend responds 200, muted dot otherwise |
| Audit log stream | `GET /api/audit` | every 2s when "Live" toggle is on | Newest 50 events, sortable/filterable |
| Overview KPIs + charts | `GET /api/audit/stats` + `GET /api/audit?limit=50` | every 5s | Live updates as new events arrive |

---

## What we log

This is the complete set of fields the dashboard renders. Every field
comes from the cloud backend's `audit_events` table — the dashboard adds
nothing, computes a few derived things (relative time, hour buckets,
totals).

### Audit event fields (per row)

| Field | Type | Source | Example | What it means |
|-------|------|--------|---------|---------------|
| `id` | int | cloud backend auto-generated | `42` | Stable event ID |
| `event_type` | string | local backend reports | `"mask"` | What happened: `mask`, `detect`, `file`, `fail_closed` |
| `entity_types` | JSON object | local backend reports | `{"PERSON": 3, "EMAIL": 2}` | Per-type count of entities touched. Counts only — never values. |
| `entity_count` | int | local backend reports | `5` | Sum of all values in `entity_types` |
| `latency_ms` | int or null | local backend reports | `23` | P95 latency for the operation in milliseconds. `null` for `fail_closed` events. |
| `timestamp` | ISO 8601 | cloud backend on receipt | `"2026-10-05T09:53:50.086348"` | When the cloud backend received the event (UTC) |

### Event types — full semantics

| `event_type` | Triggered when | `entity_types` populated? | `latency_ms` populated? |
|---|---|---|---|
| `"mask"` | The local backend successfully masked a prompt or file | Yes — counts of each entity type found | Yes — end-to-end mask latency |
| `"detect"` | The local backend ran a detect-only operation (no masking) | Yes | Yes |
| `"file"` | A file upload was processed (PDF / Word / Excel / text) | Yes — entity types found in the file's text layer | Yes — includes parse + mask + reconstruct time |
| `"fail_closed"` | The request was held (residual leak, backend down, exception) | Usually empty `{}` | Usually `null` |

### Derived display values (computed client-side, not stored)

| Display | Derived from | Computation |
|---------|--------------|------------|
| `formatTime(timestamp)` | `timestamp` | `HH:MM:SS` local time, computed via `Date.toLocaleTimeString` |
| `formatRelative(timestamp)` | `timestamp` | "just now" / "5m ago" / "2h ago" / "3d ago" |
| Hour bucket for line chart | `timestamp` | `new Date(timestamp).getHours()` |
| Donut percentages | `total_events`, `by_event_type`, `fail_closed_events` | Client-side division |
| Bar chart widths | `entity_type_breakdown` | Per-row `(count / max) * 100` |
| KPI numbers | `total_events`, `total_entities_masked`, `fail_closed_events`, `avg_latency_ms` | Direct display, formatted with thousands separators |

### Policy fields (per row, on the Policies page)

| Field | Type | Source | Example | What it means |
|-------|------|--------|---------|---------------|
| `id` | int | cloud backend | `1` | Stable policy ID |
| `org_id` | int | cloud backend | from the API key | Which org owns this policy (set by the server, never by the client) |
| `entity_type` | string | admin-set | `"PERSON"` | The type of entity this policy applies to |
| `action` | string | admin-set (toggle) | `"faker"` | What to do: `faker` (replace with stand-in), `redact` (replace with `[[REDACTED]]`), `keep` (leave alone) |
| `is_default` | bool | cloud backend | `true` | True if seeded on first boot — locked from delete |
| `version` | int | cloud backend | `2` | Bumped on each `PUT` — for audit trail |

---

## What we do NOT track or log

This is the explicit list. None of these ever appear in the dashboard, the
cloud backend's database, or any network request the dashboard makes.

| Field | Why not |
|-------|---------|
| Prompt text (the user's typed input) | Never leaves the local backend's memory |
| AI response text | Never sent to the cloud |
| Real PII values (names, emails, cards, addresses, etc.) | Stay on the user's device, in the local backend's vault |
| Masked surrogate values | Stay on the user's device |
| The (real → fake) mapping vault | On-device only, per conversation. Persisted by default in an encrypted SQLite file (24 h TTL, owner-only file permissions); set `DLP_VAULT_PERSIST=false` for memory-only. Never sent to the cloud. |
| File contents (PDF text, Word paragraphs, Excel cells) | Processed locally, only the metadata envelope is sent |
| User identity (which employee) | The dashboard doesn't show employee names — only `event_type`, `entity_types`, `latency_ms` |
| Browser history / URLs visited | The dashboard has no awareness of which AI tool was used |
| IP addresses of users | Not collected |
| Geolocation | Not collected |

The dashboard renders entity **types** (PERSON, EMAIL, CREDIT_CARD, etc.) and
**counts** (×3, ×1, ×2). It cannot tell you *which* names were sent, *which*
card numbers were masked, or *who* sent them. This is by design.

---

## Per-screen breakdown

For each of the four screens, here's exactly what's displayed and where it
comes from:

### Overview (`/`)

```
URL: http://localhost:5173/

Polls every 5 seconds:
  - GET /api/audit/stats?hours=24&org_id=1
  - GET /api/audit?limit=8

Displays:
  1. Page header (eyebrow + "Last 24 hours" + subline)
  2. 4 KPI cards (total events, entities masked, fail-closed, avg latency)
  3. Activity line chart — mask events per hour, last 12 hours
  4. Outcome split donut — masked / fail-closed / other
  5. Top entity types bar chart — top 8 types by masked count
  6. Recent events feed — 6 most recent events with timestamp, type, entity chips, latency
  7. "View all →" link to the Audit page
```

### Policies (`/policies`)

```
URL: http://localhost:5173/policies

Loads on mount (no polling):
  - GET /api/policies

Actions:
  - Click faker / redact / keep chip → PUT /api/policies/{id} with {action: "..."}
  - Click "New policy" → form with entity_type input + action dropdown
    - Save → POST /api/policies with {entity_type, action}
  - Click × on a non-default policy → DELETE /api/policies/{id}
  - Default policies show a lock icon and have disabled chips

Displays:
  1. Page header (eyebrow + "Entity-type rules" + subline)
  2. New policy button (emerald)
  3. Inline add form (when expanded)
  4. Sortable table: entity_type | action chips (faker/redact/keep) | version + delete
  5. Help text below explaining defaults are locked
```

### Audit log (`/audit`)

```
URL: http://localhost:5173/audit

Polls every 2 seconds (when "Live" toggle is on):
  - GET /api/audit?limit=50&event_type={filter}

Filter chips:
  - All → no event_type param
  - Masked → event_type=mask
  - Detected → event_type=detect
  - File upload → event_type=file
  - Fail-closed → event_type=fail_closed

Actions:
  - Click × on a row → DELETE /api/audit/{id} (GDPR erasure)

Displays:
  1. Page header (eyebrow + "Intercepted events" + subline)
  2. Live/Paused toggle (top right)
  3. Filter chip row
  4. Event table: time | type | entity chips + relative time | latency | delete button
  5. Footer count (X of last 50 events shown)
```

### Settings (`/settings`)

```
URL: http://localhost:5173/settings

Loads on mount:
  - GET /health (then every 10s)
  - GET /api/policies/export

Displays (read-only):
  1. Page header (eyebrow + "Organization" + subline)
  2. Org card — name (Acme Inc.), org ID (1), endpoints enrolled (1)
  3. Cloud backend connection — URL, health status, how to change URL
  4. Per-org API key — password input, stored for the tab session only
  5. Exported policy snapshot — the live JSON the cloud backend would serve
     to a local backend pulling from /api/policies/export
```

---

## Tech stack

- **Framework:** Vite 5 + React 18
- **Language:** TypeScript 5
- **Routing:** React Router 6 (4 routes)
- **Styling:** Tailwind CSS 4 (CSS-based config via `@theme inline`)
- **Charts:** Recharts 2 (line, donut, bar)
- **Icons:** Lucide React
- **Animations:** Framer Motion (subtle scroll-reveal + chip transitions only)
- **Fonts:** Fraunces (serif headlines) + Inter (body) + JetBrains Mono (code)
  — loaded from Google Fonts via `index.html`

---

## Setup

### Prerequisites

- Node.js 18+ (or Bun 1.0+)
- The cloud backend running on `http://localhost:8000` (or your customer's
  server URL — see below)

### Install

```bash
cd dashboard
npm install   # or: bun install
cp .env.example .env
```

### Configure the backend URL

Edit `.env`:

```
# For local dev (cloud backend running on your machine)
VITE_CLOUD_BACKEND_URL=http://localhost:8000

# For customer deployment (their cloud backend URL)
VITE_CLOUD_BACKEND_URL=https://doppel-cloud.acme.io
```

The dashboard makes all requests with CORS, so the cloud backend must list
your dashboard's origin in its `CORS_ORIGINS` env var. For local dev, leave
the cloud backend's `CORS_ORIGINS=*` (the default).

### Run

```bash
npm run dev
# → http://localhost:5173
```

### Build for production

```bash
npm run build
# Output: dist/ (static files — deploy anywhere)
```

Deploy `dist/` to any static host (nginx, S3 + CloudFront, Vercel, Netlify).
No server-side runtime needed — it's just HTML + JS + CSS.

---

## Configuration

| Env var | Default | Required | Notes |
|---------|---------|----------|-------|
| `VITE_CLOUD_BACKEND_URL` | `http://localhost:8000` | Yes | The cloud backend's URL. The dashboard makes all API calls relative to this. |

To change env vars after building, rebuild — Vite inlines them at build time.

---

## Data sources

The dashboard's data layer is `src/api/client.ts` — a single file with one
typed function per cloud backend endpoint. Read it to see exactly what's
called, what's sent, and what's returned.

```
src/api/client.ts → fetch(`VITE_CLOUD_BACKEND_URL${path}`)
  ├── health()
  ├── listPolicies(orgId)
  ├── getPolicy(entityType, orgId)
  ├── createPolicy(body)
  ├── updatePolicy(id, body)
  ├── deletePolicy(id)
  ├── exportPolicies(orgId)
  ├── listAudit(params)
  ├── auditStats(hours, orgId)
  ├── submitAudit(body)   # not used by the dashboard — for completeness
  └── deleteAudit(id)
```

Every function makes exactly one HTTP request. No batching, no caching, no
background sync beyond the polling intervals listed in [What we track](#what-we-track).

---

## Compliance notes

### GDPR

- **Right to access:** an admin can export the full audit log via
  `GET /api/audit?limit=...` and the policy set via
  `GET /api/policies/export`. Both are JSON.
- **Right to erasure:** the Audit page exposes a per-event delete button that
  calls `DELETE /api/audit/{id}`. Bulk delete via the API directly.
- **Data minimization:** the dashboard only collects what it needs for
  operational metrics — entity types + counts + timing. No user identity.

### EU AI Act

The dashboard surfaces audit metadata suitable for the transparency and
logging requirements of high-risk AI systems. The per-event entity-type
breakdown supports the "adequate logging" requirement.

### HIPAA

The dashboard does not store Protected Health Information (PHI). If a local
backend detects PHI (e.g., medical record numbers — add a `MEDICAL_RECORD`
entity type to the policies), the audit event will record only the count
("1 MEDICAL_RECORD entity masked") — never the value.

### SOC 2

The audit log provides the immutable record of security-relevant events
required for SOC 2 Type II. Pair with the cloud backend's TLS termination
API key auth is implemented: every request carries `X-API-Key`.

---

## What's NOT in the dashboard (intentionally)

- **Auth:** The dashboard sends the organization's API key (`X-API-Key`) on every request.
  Enter it in **Settings**; it's kept in `sessionStorage` (cleared when the tab closes). The
  organization is derived from the key server-side. There is no per-user login yet — anyone
  with the key has admin access to that org. Create keys with
  `python -m app.admin create-org "<name>"` on the cloud backend.
- **Multi-org switcher:** Not needed — the key selects the org. Add a dropdown when you
  have multiple orgs.
- **WebSocket:** Audit log polls every 2s. Switch to WS when you need
  real-time push (the cloud backend doesn't expose WS yet).
- **Mobile-specific tuning:** Desktop-first. The sidebar hides below `lg`
  but the main content stays usable.

---

## Troubleshooting

### "Can't reach the cloud backend"

The cloud backend is offline or unreachable. Check:
1. The cloud backend is running — `curl $VITE_CLOUD_BACKEND_URL/health`
2. The URL in `.env` is correct — `cat .env`
3. CORS is configured — the cloud backend's `CORS_ORIGINS` env var includes
   your dashboard's origin

### KPIs show 0 but audit log has events

The stats endpoint uses `?hours=24` by default. If your events are older
than 24 hours, they won't show in the KPIs but will still appear in the audit
log (which lists everything, no time filter).

### Policies chips are disabled / locked

Default policies (seeded on first boot of the cloud backend) cannot be
deleted — they show a lock icon. You can still toggle their action
(faker/redact/keep) — the version bumps on each save.

### Audit log shows no new events

The dashboard polls every 2s when "Live" is on. If events aren't appearing:
1. Local backends aren't running on any employee device
2. Local backends are running but pointing at a different cloud backend URL
3. Cloud backend's audit endpoint isn't accepting POSTs — check the cloud
   backend logs for errors

To generate test events for the demo, POST to the cloud backend directly:

```bash
curl -X POST http://localhost:8000/api/audit \
  -H "Content-Type: application/json" \
  -d '{"event_type":"mask","entity_types":{"PERSON":2},"entity_count":2,"latency_ms":23}'
```

The event will appear in the dashboard within 2 seconds.

---

## License

See the parent repo's LICENSE.
