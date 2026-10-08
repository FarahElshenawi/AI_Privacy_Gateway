/**
 * API client — talks to the cloud backend (FastAPI in cloud-backend/).
 *
 * The base URL is read from VITE_CLOUD_BACKEND_URL (set in .env). For local dev
 * this is http://localhost:8000. For customer deployments, it's their own
 * server URL. The Vite dev server also proxies /api/* to the backend, so
 * relative paths work too.
 *
 * Every function here maps 1:1 to an endpoint in cloud-backend/app/api/.
 */

const BASE_URL =
  (import.meta as any).env?.VITE_CLOUD_BACKEND_URL || "/api";

// Strip a trailing "/api" if someone accidentally included it in the env var
const API_BASE = BASE_URL.endsWith("/api")
  ? BASE_URL.slice(0, -4)
  : BASE_URL;

const KEY_STORAGE = "doppel_api_key";

/** The org API key is kept in sessionStorage (cleared when the tab closes), never in the bundle. */
export function getApiKey(): string {
  try { return sessionStorage.getItem(KEY_STORAGE) || ""; } catch { return ""; }
}
export function setApiKey(key: string): void {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key.trim());
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch { /* storage unavailable */ }
}

export class AuthError extends Error {
  constructor(message: string) { super(message); this.name = "AuthError"; }
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(getApiKey() ? { "X-API-Key": getApiKey() } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      throw new AuthError(getApiKey() ? "API key rejected — check Settings." : "Enter your API key in Settings.");
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ===== Types =====

export type PolicyAction = "faker" | "redact" | "keep" | "block";

export interface Policy {
  id: number;
  org_id: number;
  entity_type: string;
  action: PolicyAction;
  is_default: boolean;
  version: number;
}

export interface PolicyCreate {
  org_id?: number;
  entity_type: string;
  action: PolicyAction;
}

export interface PolicyUpdate {
  action?: PolicyAction;
  entity_type?: string;
}

export interface AuditEvent {
  id: number;
  event_type: "mask" | "detect" | "file" | "fail_closed";
  entity_types: Record<string, number> | null;
  entity_count: number;
  latency_ms: number | null;
  timestamp: string;
}

export interface AuditStats {
  time_window_hours: number;
  total_events: number;
  by_event_type: Record<string, number>;
  fail_closed_events: number;
  avg_latency_ms: number | null;
  total_entities_masked: number;
  entity_type_breakdown: Record<string, number>;
}

export interface HealthStatus {
  status: string;
  service: string;
  version: string;
}

// ===== Endpoints =====

export const api = {
  /** Health check — used to confirm the cloud backend is reachable. */
  health: () => request<HealthStatus>("/health"),

  // ----- Policies -----

  /** List all policies for the org (default org_id=1). */
  listPolicies: (orgId = 1) =>
    request<Policy[]>(`/api/policies?org_id=${orgId}`),

  /** Get the policy for a specific entity type. */
  getPolicy: (entityType: string, orgId = 1) =>
    request<Policy>(`/api/policies/${entityType}?org_id=${orgId}`),

  /** Create a new policy. */
  createPolicy: (body: PolicyCreate) =>
    request<Policy>("/api/policies", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Update an existing policy (action or entity_type). */
  updatePolicy: (id: number, body: PolicyUpdate) =>
    request<Policy>(`/api/policies/${id}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  /** Delete a policy. */
  deletePolicy: (id: number) =>
    request<void>(`/api/policies/${id}`, { method: "DELETE" }),

  /** Export all policies as JSON (for the local backend pull). */
  exportPolicies: (orgId = 1) =>
    request<Record<string, string>>(`/api/policies/export?org_id=${orgId}`),

  /** Apply exported policies directly to the running local-backend gateway. */
  syncPoliciesToLocalGateway: async (localBaseUrl = "http://127.0.0.1:8765") => {
    const exported = await api.exportPolicies();
    const res = await fetch(`${localBaseUrl}/api/policies/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ OVERRIDES: exported }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to sync policies to local gateway");
    }
    return res.json() as Promise<{ status: string; applied: number }>;
  },

  // ----- Audit -----

  /** List audit events (optionally filtered by event_type). */
  listAudit: (params?: { orgId?: number; eventType?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.orgId) q.set("org_id", String(params.orgId));
    if (params?.eventType) q.set("event_type", params.eventType);
    if (params?.limit) q.set("limit", String(params.limit));
    return request<AuditEvent[]>(`/api/audit?${q.toString()}`);
  },

  /** Aggregated stats for the dashboard overview. */
  auditStats: (hours = 24, orgId = 1) =>
    request<AuditStats>(`/api/audit/stats?hours=${hours}&org_id=${orgId}`),

  /** Submit a new audit event (used by the local backend, not the dashboard). */
  submitAudit: (body: Omit<AuditEvent, "id" | "timestamp">) =>
    request<AuditEvent>("/api/audit", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Delete an audit event (GDPR right to erasure). */
  deleteAudit: (id: number) =>
    request<void>(`/api/audit/${id}`, { method: "DELETE" }),
};
