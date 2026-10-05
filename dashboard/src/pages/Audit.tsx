import { useEffect, useRef, useState } from "react";
import { api, type AuditEvent } from "../api/client";
import { formatTime, formatRelative } from "../lib/utils";

const FILTERS: { label: string; value: "" | "mask" | "detect" | "file" | "fail_closed" }[] = [
  { label: "All", value: "" },
  { label: "Masked", value: "mask" },
  { label: "Detected", value: "detect" },
  { label: "File upload", value: "file" },
  { label: "Fail-closed", value: "fail_closed" },
];

export function Audit() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState<"" | "mask" | "detect" | "file" | "fail_closed">("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const lastIdRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const list = await api.listAudit({
          eventType: filter || undefined,
          limit: 50,
        });
        if (cancelled) return;
        // Newest first
        list.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        setEvents(list);
        if (list.length > 0) lastIdRef.current = list[0].id;
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    if (!autoRefresh) return;
    const interval = setInterval(load, 2000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [filter, autoRefresh]);

  const removeEvent = async (id: number) => {
    try {
      await api.deleteAudit(id);
      setEvents((prev) => prev.filter((e) => e.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <div className="max-w-[1100px] mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Audit log</span>
          <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
            Intercepted events
          </h1>
          <p className="mt-1 text-[14px] text-[var(--body)] max-w-[520px]">
            Metadata only — entity types, counts, latency. Never prompt content.
          </p>
        </div>
        <button
          className={`btn-secondary flex items-center gap-2 flex-shrink-0 ${
            autoRefresh ? "" : ""
          }`}
          onClick={() => setAutoRefresh((v) => !v)}
          style={
            autoRefresh
              ? { background: "var(--subtle)", borderColor: "var(--primary)", color: "var(--primary)" }
              : undefined
          }
        >
          <span
            className={`status-dot ${autoRefresh ? "" : "muted"}`}
            style={autoRefresh ? undefined : { animation: "none" }}
          />
          <span>{autoRefresh ? "Live" : "Paused"}</span>
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            className="btn-ghost"
            style={
              filter === f.value
                ? {
                    background: "var(--subtle)",
                    color: "var(--ink)",
                    border: "1px solid var(--border)",
                  }
                : undefined
            }
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="card p-4 border-l-2" style={{ borderLeftColor: "var(--danger)" }}>
          <p className="text-[13px] text-[var(--danger)]">{error}</p>
        </div>
      )}

      {/* Event table */}
      <div className="card overflow-hidden">
        <div className="grid grid-cols-[90px_120px_1fr_120px_60px] gap-4 px-5 py-3.5 border-b border-[var(--border)] bg-[var(--subtle)] text-[10.5px] mono uppercase tracking-[0.18em] text-[var(--muted)]">
          <div>Time</div>
          <div>Type</div>
          <div>Entities caught</div>
          <div className="text-right">Latency</div>
          <div></div>
        </div>
        <div className="min-h-[300px] max-h-[600px] overflow-y-auto">
          {loading ? (
            <div className="py-16 text-center text-[13px] text-[var(--muted)]">
              Loading…
            </div>
          ) : events.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-[var(--muted)]">
              No events match the current filter.
            </div>
          ) : (
            events.map((e) => {
              const isFail = e.event_type === "fail_closed";
              return (
                <div
                  key={e.id}
                  className="grid grid-cols-[90px_120px_1fr_120px_60px] gap-4 px-5 py-3.5 items-center border-b border-[var(--border)] last:border-b-0 hover:bg-[var(--subtle)]/40 transition-colors reveal"
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`status-dot ${
                        isFail ? "danger" : e.event_type === "mask" ? "" : "muted"
                      }`}
                    />
                    <span className="mono text-[11px] text-[var(--muted)]" title={new Date(e.timestamp).toLocaleString()}>
                      {formatTime(e.timestamp)}
                    </span>
                  </div>
                  <div>
                    <span
                      className="text-[12px] font-semibold mono"
                      style={{
                        color: isFail ? "var(--danger)" : "var(--ink)",
                      }}
                    >
                      {e.event_type}
                    </span>
                  </div>
                  <div className="min-w-0">
                    {e.entity_types ? (
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(e.entity_types).map(([k, v]) => (
                          <span
                            key={k}
                            className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] mono border"
                            style={{
                              color:
                                k === "API_KEY" || k === "JWT" || k === "PEM_BLOCK" || k === "IBAN" || k === "CREDIT_CARD" || k === "SSN"
                                  ? "var(--danger)"
                                  : "var(--primary)",
                              borderColor:
                                k === "API_KEY" || k === "JWT" || k === "PEM_BLOCK" || k === "IBAN" || k === "CREDIT_CARD" || k === "SSN"
                                  ? "rgba(184, 84, 80, 0.3)"
                                  : "rgba(47, 213, 143, 0.3)",
                              background:
                                k === "API_KEY" || k === "JWT" || k === "PEM_BLOCK" || k === "IBAN" || k === "CREDIT_CARD" || k === "SSN"
                                  ? "rgba(184, 84, 80, 0.06)"
                                  : "rgba(47, 213, 143, 0.06)",
                            }}
                          >
                            {k} ×{v}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-[12px] text-[var(--muted)]">—</span>
                    )}
                    <div className="text-[11px] text-[var(--muted)] mt-0.5">
                      {formatRelative(e.timestamp)}
                    </div>
                  </div>
                  <div className="text-right mono text-[11px] text-[var(--muted)]">
                    {e.latency_ms != null ? `${e.latency_ms}ms` : "—"}
                  </div>
                  <div className="text-right">
                    <button
                      className="btn-ghost p-1.5"
                      title="Delete event (GDPR erasure)"
                      onClick={() => removeEvent(e.id)}
                    >
                      ×
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      <p className="text-[12px] text-[var(--muted)] mt-3">
        Showing {events.length} of last 50 events. Auto-refresh polls every 2s.
      </p>
    </div>
  );
}
