import { useEffect, useState } from "react";
import { api, type Policy } from "../api/client";
import { Plus, Trash2, Lock } from "lucide-react";

const ACTIONS: Policy["action"][] = ["faker", "redact", "keep"];

export function Policies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newType, setNewType] = useState("");
  const [newAction, setNewAction] = useState<Policy["action"]>("faker");

  const load = async () => {
    try {
      const list = await api.listPolicies(1);
      // Sort: redact first (secrets), then faker (PII), then keep (preserve)
      const order = { redact: 0, faker: 1, keep: 2 };
      list.sort((a, b) => order[a.action] - order[b.action]);
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
    try {
      await api.updatePolicy(p.id, { action });
      setPolicies((prev) =>
        prev.map((x) => (x.id === p.id ? { ...x, action } : x))
      );
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
    if (!newType.trim()) return;
    try {
      const created = await api.createPolicy({
        org_id: 1,
        entity_type: newType.trim().toUpperCase(),
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
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="eyebrow">Policies</span>
          <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
            Entity-type rules
          </h1>
          <p className="mt-1 text-[14px] text-[var(--body)] max-w-[520px]">
            Each row decides what happens when a sensitive entity of that type is
            detected. <span className="text-[var(--ink)]">Faker</span> swaps it
            for a realistic stand-in. <span className="text-[var(--ink)]">Redact</span>{" "}
            replaces it with <code className="mono">[[REDACTED]]</code>.{" "}
            <span className="text-[var(--ink)]">Keep</span> leaves it untouched.
          </p>
        </div>
        {!adding && (
          <button
            className="btn-primary flex items-center gap-2 flex-shrink-0"
            onClick={() => setAdding(true)}
          >
            <Plus className="h-4 w-4" />
            <span>New policy</span>
          </button>
        )}
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
              placeholder="Entity type, e.g. PASSPORT_NUMBER"
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
            policies.map((p, i) => (
              <div
                key={p.id}
                className="grid grid-cols-[1.4fr_1fr_auto] gap-4 px-5 py-3.5 items-center border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--subtle)]/40 transition-colors"
                style={{ animationDelay: `${i * 20}ms` }}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="mono text-[13px] text-[var(--ink)] truncate">
                    {p.entity_type}
                  </span>
                  {p.is_default && (
                    <span className="inline-flex items-center gap-1 text-[10px] mono uppercase tracking-wider text-[var(--muted)] flex-shrink-0">
                      <Lock className="h-3 w-3" />
                      default
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  {ACTIONS.map((a) => (
                    <button
                      key={a}
                      className={`chip ${a === p.action ? a : ""}`}
                      onClick={() => updateAction(p, a)}
                      disabled={p.is_default}
                      style={
                        p.is_default
                          ? { opacity: 0.6, cursor: "not-allowed" }
                          : undefined
                      }
                    >
                      {a}
                    </button>
                  ))}
                </div>
                <div className="flex items-center justify-end gap-2">
                  <span className="text-[11px] mono text-[var(--muted)]">
                    v{p.version}
                  </span>
                  {!p.is_default && (
                    <button
                      className="btn-ghost p-1.5"
                      title="Delete policy"
                      onClick={() => removePolicy(p)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <p className="text-[12px] text-[var(--muted)] mt-3">
        Default policies are seeded by the cloud backend on first boot and
        cannot be deleted. Override their action by clicking the chips above —
        the version bumps on each save.
      </p>
    </div>
  );
}
