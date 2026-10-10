import { useEffect, useState } from "react";
import { api, type Policy, type PolicyChange } from "../api/client";
import { formatRelative } from "../lib/utils";
import { Plus, Trash2, Lock, ShieldCheck } from "lucide-react";

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
  const [adding, setAdding] = useState(false);
  const [newType, setNewType] = useState("");
  const [newAction, setNewAction] = useState<Policy["action"]>("faker");

  const [history, setHistory] = useState<PolicyChange[]>([]);
  const loadHistory = () => api.policyHistory(15).then(setHistory).catch(() => setHistory([]));

  const load = async () => {
    loadHistory();
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

      {/* Change history */}
      <div className="card p-5">
        <span className="eyebrow">Change history</span>
        {history.length === 0 ? (
          <p className="mt-2 text-[13px] text-[var(--muted)]">No changes recorded yet.</p>
        ) : (
          <div className="mt-2 divide-y divide-[var(--border)]">
            {history.map((h) => (
              <div key={h.id} className="flex items-baseline gap-3 py-2 text-[12.5px]">
                <span className="mono text-[var(--ink)]">{h.entity_type}</span>
                <span className="mono text-[var(--muted)] flex-1">
                  {(h.old_action ?? "(new)")} → {(h.new_action ?? "(removed)")}
                </span>
                <span className="text-[var(--muted)]">{formatRelative(h.changed_at)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="p-4 card bg-[var(--subtle)]/30 border-dashed text-[12px] text-[var(--muted)] space-y-1">
        <p>
          <strong className="text-[var(--ink)]">How it works:</strong> Clicking any action chip immediately updates the policy in the cloud backend.
        </p>
        <p>
          Each device's local backend pulls the policies from the cloud backend on a timer (default every 5 minutes), so changes reach gateways without any manual step.
        </p>
        <p>
          <strong className="text-amber-400">Security Guard:</strong> Critical secrets (API keys, credentials, government IDs) have the <code className="mono">keep</code> option disabled to guarantee security.
        </p>
      </div>
    </div>
  );
}
