import { useEffect, useState } from "react";
import { api, type Policy } from "../api/client";
import { Plus, Trash2, Lock, RefreshCw, CheckCircle2, AlertTriangle, ShieldCheck } from "lucide-react";

const ACTIONS: Policy["action"][] = ["faker", "redact", "keep", "block"];

const IMMUTABLE_CRITICAL_SECRETS = new Set([
  "API_KEY",
  "AUTH_TOKEN",
  "JWT",
  "PRIVATE_KEY",
  "CLOUD_SECRET",
  "CONNECTION_STRING",
  "PASSWORD",
  "RECOVERY_CODE",
  "CREDIT_CARD",
  "CVV",
  "US_SSN",
  "TAX_ID",
  "MEDICAL_RECORD_NUMBER",
  "HEALTH_INSURANCE_ID",
  "GOVERNMENT_ID",
  "PASSPORT_NUMBER",
  "DRIVERS_LICENSE_NUMBER",
  "SSN",
]);

export function Policies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<{ success: boolean; message: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const [newType, setNewType] = useState("");
  const [newAction, setNewAction] = useState<Policy["action"]>("faker");

  const load = async () => {
    try {
      const list = await api.listPolicies(1);
      // Sort: block first (critical), then redact (secrets), then faker (PII), then keep (preserve)
      const order: Record<Policy["action"], number> = { block: 0, redact: 1, faker: 2, keep: 3 };
      list.sort((a, b) => (order[a.action] ?? 2) - (order[b.action] ?? 2));
      setPolicies(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const updateAction = async (p: Policy, action: Policy["action"]) => {
    if (action === "keep" && IMMUTABLE_CRITICAL_SECRETS.has(p.entity_type)) {
      setError(`Security guard: Critical secret '${p.entity_type}' cannot be configured to 'keep'.`);
      return;
    }
    try {
      await api.updatePolicy(p.id, { action });
      setPolicies((prev) =>
        prev.map((x) => (x.id === p.id ? { ...x, action, version: x.version + 1 } : x))
      );
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const syncToGateway = async () => {
    setSyncing(true);
    setSyncStatus(null);
    try {
      const res = await api.syncPoliciesToLocalGateway();
      setSyncStatus({
        success: true,
        message: `Successfully synchronized ${res.applied} policies with local gateway routing engine!`,
      });
    } catch (e) {
      setSyncStatus({
        success: false,
        message: e instanceof Error ? e.message : "Failed to sync to local gateway (make sure backend is running on :8765)",
      });
    } finally {
      setSyncing(false);
    }
  };

  const removePolicy = async (p: Policy) => {
    if (p.is_default) return;
    try {
      await api.deletePolicy(p.id);
      setPolicies((prev) => prev.filter((x) => x.id !== p.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const createPolicy = async () => {
    const trimmed = newType.trim().toUpperCase();
    if (!trimmed) return;
    if (newAction === "keep" && IMMUTABLE_CRITICAL_SECRETS.has(trimmed)) {
      setError(`Security guard: Critical secret '${trimmed}' cannot be configured to 'keep'.`);
      return;
    }
    try {
      const created = await api.createPolicy({
        org_id: 1,
        entity_type: trimmed,
        action: newAction,
      });
      setPolicies((prev) => [...prev, created]);
      setNewType("");
      setNewAction("faker");
      setAdding(false);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-[var(--muted)] text-[14px]">
        Loading…
      </div>
    );
  }

  return (
    <div className="max-w-[1100px] mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div>
          <span className="eyebrow">Policies</span>
          <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
            Deterministic Routing Rules
          </h1>
          <p className="mt-1 text-[14px] text-[var(--body)] max-w-[560px]">
            Configure what happens when sensitive entities are detected.
            <span className="text-[var(--ink)] font-medium"> Faker</span> swaps for a realistic surrogate,
            <span className="text-[var(--ink)] font-medium"> Redact</span> strips the value,
            <span className="text-[var(--ink)] font-medium"> Keep</span> leaves it intact, and
            <span className="text-[var(--ink)] font-medium"> Block</span> halts the request.
          </p>
        </div>
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <button
            className="btn-secondary flex items-center gap-2"
            onClick={syncToGateway}
            disabled={syncing}
            title="Export cloud policies and apply to the local gateway routing engine (port 8765)"
          >
            <RefreshCw className={`h-4 w-4 ${syncing ? "animate-spin" : ""}`} />
            <span>{syncing ? "Syncing…" : "Sync to Gateway"}</span>
          </button>
          {!adding && (
            <button
              className="btn-primary flex items-center gap-2"
              onClick={() => setAdding(true)}
            >
              <Plus className="h-4 w-4" />
              <span>New policy</span>
            </button>
          )}
        </div>
      </div>

      {/* Sync Status Banner */}
      {syncStatus && (
        <div
          className={`card p-4 border-l-4 flex items-center justify-between gap-3 ${
            syncStatus.success ? "border-l-emerald-500 bg-emerald-500/5" : "border-l-rose-500 bg-rose-500/5"
          }`}
        >
          <div className="flex items-center gap-2.5">
            {syncStatus.success ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-400 flex-shrink-0" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-rose-400 flex-shrink-0" />
            )}
            <p className={`text-[13px] ${syncStatus.success ? "text-emerald-300" : "text-rose-300"}`}>
              {syncStatus.message}
            </p>
          </div>
          <button
            className="text-[12px] text-[var(--muted)] hover:text-[var(--ink)] mono"
            onClick={() => setSyncStatus(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {error && (
        <div className="card p-4 border-l-2" style={{ borderLeftColor: "var(--danger)" }}>
          <p className="text-[13px] text-[var(--danger)]">{error}</p>
        </div>
      )}

      {/* Add new row */}
      {adding && (
        <div className="card p-5 reveal">
          <span className="eyebrow">New policy</span>
          <div className="mt-3 flex flex-col sm:flex-row gap-3">
            <input
              className="input flex-1"
              placeholder="Entity type, e.g. CUSTOM_IDENTIFIER"
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createPolicy()}
              autoFocus
            />
            <select
              className="input sm:w-40"
              value={newAction}
              onChange={(e) => setNewAction(e.target.value as Policy["action"])}
            >
              <option value="faker">faker</option>
              <option value="redact">redact</option>
              <option value="keep">keep</option>
              <option value="block">block</option>
            </select>
            <button className="btn-primary" onClick={createPolicy}>
              Save
            </button>
            <button
              className="btn-secondary"
              onClick={() => {
                setAdding(false);
                setNewType("");
                setNewAction("faker");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Policies table */}
      <div className="card overflow-hidden">
        <div className="grid grid-cols-[1.4fr_1fr_auto] gap-4 px-5 py-3.5 border-b border-[var(--border)] bg-[var(--subtle)] text-[10.5px] mono uppercase tracking-[0.18em] text-[var(--muted)]">
          <div>Entity type</div>
          <div>Action</div>
          <div className="text-right">Status</div>
        </div>
        <div>
          {policies.length === 0 ? (
            <div className="py-12 text-center text-[13px] text-[var(--muted)]">
              No policies yet. Click <strong className="text-[var(--ink)]">New policy</strong> to add one.
            </div>
          ) : (
            policies.map((p, i) => {
              const isImmutableSecret = IMMUTABLE_CRITICAL_SECRETS.has(p.entity_type);
              return (
                <div
                  key={p.id}
                  className="grid grid-cols-[1.4fr_1fr_auto] gap-4 px-5 py-3.5 items-center border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--subtle)]/40 transition-colors"
                  style={{ animationDelay: `${i * 20}ms` }}
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="mono text-[13px] text-[var(--ink)] truncate">
                      {p.entity_type}
                    </span>
                    {isImmutableSecret ? (
                      <span
                        className="inline-flex items-center gap-1 text-[10px] mono uppercase tracking-wider text-amber-400/80 bg-amber-400/10 px-1.5 py-0.5 rounded flex-shrink-0"
                        title="Critical secret: cannot be configured with 'keep'"
                      >
                        <ShieldCheck className="h-3 w-3" />
                        guarded
                      </span>
                    ) : p.is_default ? (
                      <span className="inline-flex items-center gap-1 text-[10px] mono uppercase tracking-wider text-[var(--muted)] flex-shrink-0">
                        <Lock className="h-3 w-3" />
                        default
                      </span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {ACTIONS.map((a) => {
                      const isDisabled = a === "keep" && isImmutableSecret;
                      return (
                        <button
                          key={a}
                          className={`chip ${a === p.action ? a : ""}`}
                          onClick={() => !isDisabled && updateAction(p, a)}
                          disabled={isDisabled}
                          title={
                            isDisabled
                              ? "Security constraint: critical secret cannot be set to keep"
                              : `Set action to ${a}`
                          }
                          style={
                            isDisabled
                              ? { opacity: 0.25, cursor: "not-allowed" }
                              : undefined
                          }
                        >
                          {a}
                        </button>
                      );
                    })}
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    <span className="text-[11px] mono text-[var(--muted)]">
                      v{p.version}
                    </span>
                    {!p.is_default && (
                      <button
                        className="btn-ghost p-1.5"
                        title="Delete custom policy"
                        onClick={() => removePolicy(p)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* System-Managed Dynamic Routing Card */}
      <div className="card p-5 border border-sky-500/20 bg-sky-500/5">
        <div className="flex items-center gap-2">
          <span className="eyebrow text-sky-400">System-Managed Routing (Value-Dependent)</span>
        </div>
        <div className="mt-2.5 space-y-1.5 text-[13px]">
          <div className="flex items-baseline gap-2">
            <span className="mono font-semibold text-[var(--ink)]">IP_ADDRESS / IPV4 / IPV6</span>
            <span className="text-[10px] mono text-sky-400 uppercase tracking-wider bg-sky-500/10 px-2 py-0.5 rounded border border-sky-500/20">
              RFC 6890 / 8190 Offline Classifier
            </span>
          </div>
          <p className="text-[12.5px] leading-relaxed text-[var(--muted)]">
            IP addresses are evaluated dynamically at runtime based on their actual value rather than a static entity rule:
            globally routable public IPs are preserved (<code className="mono text-[var(--ink)]">KEEP</code>), while private, loopback, CGNAT, link-local, and unparseable IPs are stripped (<code className="mono text-[var(--danger)]">REDACT</code>).
          </p>
        </div>
      </div>

      <div className="p-4 card bg-[var(--subtle)]/30 border-dashed text-[12px] text-[var(--muted)] space-y-1">
        <p>
          <strong className="text-[var(--ink)]">How it works:</strong> Clicking any action chip immediately updates the policy in the Cloud Backend.
        </p>
        <p>
          Click <strong className="text-[var(--ink)]">Sync to Gateway</strong> to push all active policies directly to your local gateway runtime (<code className="mono">http://127.0.0.1:8765/api/policies/apply</code>).
        </p>
        <p>
          <strong className="text-amber-400">Security Guard:</strong> Critical secrets (API keys, credentials, government IDs) have the <code className="mono">keep</code> option disabled to guarantee security.
        </p>
      </div>
    </div>
  );
}
