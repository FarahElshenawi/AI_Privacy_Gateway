import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { api, type AuditStats, type AuditEvent } from "../api/client";
import { KpiCard } from "../components/KpiCard";
import { ShieldCheck, Ban, Activity, Clock } from "lucide-react";
import { formatNumber, formatTime } from "../lib/utils";

export function Overview() {
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [recent, setRecent] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, r] = await Promise.all([
          api.auditStats(24, 1),
          api.listAudit({ limit: 8 }),
        ]);
        if (cancelled) return;
        setStats(s);
        setRecent(r);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Build the masked-vs-held donut
  const donutData = stats
    ? [
        {
          name: "Masked",
          value:
            stats.by_event_type.mask || 0,
          color: "var(--primary)",
        },
        {
          name: "Held (fail-closed)",
          value: stats.fail_closed_events || 0,
          color: "var(--danger)",
        },
        {
          name: "Other",
          value:
            (stats.total_events || 0) -
            (stats.by_event_type.mask || 0) -
            (stats.fail_closed_events || 0),
          color: "var(--muted)",
        },
      ].filter((d) => d.value > 0)
    : [];

  // Build the entity-type breakdown chart
  const entityBars = stats
    ? Object.entries(stats.entity_type_breakdown)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([type, count]) => ({
          type,
          count,
        }))
    : [];

  // Build a fake-but-stable time series for masked events (the stats endpoint
  // returns aggregates, not buckets, so we approximate using recent events).
  // The /api/audit endpoint returns recent events; we bucket by hour.
  const buckets: { hour: string; value: number }[] = [];
  const now = Date.now();
  for (let i = 11; i >= 0; i--) {
    const t = new Date(now - i * 60 * 60 * 1000);
    buckets.push({ hour: `${t.getHours()}h`, value: 0 });
  }
  recent.forEach((e) => {
    if (e.event_type !== "mask") return;
    const t = new Date(e.timestamp).getHours();
    const idx = buckets.findIndex((b) => parseInt(b.hour) === t);
    if (idx >= 0) buckets[idx].value += 1;
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-[var(--muted)] text-[14px]">
        Loading…
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState
        message={error}
        onRetry={() => {
          setLoading(true);
          window.location.reload();
        }}
      />
    );
  }

  return (
    <div className="max-w-[1100px] mx-auto space-y-8">
      {/* Header */}
      <div>
        <span className="eyebrow">Overview</span>
        <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
          Last 24 hours
        </h1>
        <p className="mt-1 text-[14px] text-[var(--body)]">
          Aggregated activity across all enrolled local backends.
        </p>
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard
          label="Total events"
          value={formatNumber(stats?.total_events || 0)}
          sub="last 24 hours"
          icon={<Activity className="h-4 w-4" />}
        />
        <KpiCard
          label="Entities masked"
          value={formatNumber(stats?.total_entities_masked || 0)}
          tone="primary"
          sub="across all masked events"
          icon={<ShieldCheck className="h-4 w-4" />}
        />
        <KpiCard
          label="Fail-closed events"
          value={formatNumber(stats?.fail_closed_events || 0)}
          tone="danger"
          sub="requests blocked, not leaked"
          icon={<Ban className="h-4 w-4" />}
        />
        <KpiCard
          label="Avg latency"
          value={
            stats?.avg_latency_ms
              ? `${Math.round(stats.avg_latency_ms)}ms`
              : "—"
          }
          sub="P95 across mask events"
          icon={<Clock className="h-4 w-4" />}
        />
      </div>

      {/* Charts */}
      <div className="grid lg:grid-cols-[1.4fr_1fr] gap-4">
        {/* Time-series chart */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <span className="eyebrow">Activity</span>
              <h3 className="mt-1 text-[15px] font-semibold text-[var(--ink)]">
                Masked events per hour
              </h3>
            </div>
            <span className="status-dot" />
          </div>
          <div style={{ width: "100%", height: 220 }}>
            <ResponsiveContainer>
              <LineChart
                data={buckets}
                margin={{ top: 10, right: 10, bottom: 0, left: -20 }}
              >
                <CartesianGrid
                  strokeDasharray="2 4"
                  stroke="var(--border)"
                  vertical={false}
                />
                <XAxis
                  dataKey="hour"
                  tick={{ fontSize: 11 }}
                  stroke="var(--border)"
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  stroke="var(--border)"
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    color: "var(--ink)",
                    fontFamily: "var(--font-jetbrains-mono)",
                    fontSize: 12,
                  }}
                  cursor={false}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="var(--primary)"
                  strokeWidth={2}
                  dot={{ r: 3, fill: "var(--primary)", strokeWidth: 0 }}
                  activeDot={{ r: 5, fill: "var(--primary)" }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Donut chart */}
        <div className="card p-5">
          <span className="eyebrow">Outcome split</span>
          <h3 className="mt-1 mb-4 text-[15px] font-semibold text-[var(--ink)]">
            Masked vs held vs other
          </h3>
          {donutData.length > 0 ? (
            <>
              <div style={{ width: "100%", height: 180 }}>
                <ResponsiveContainer>
                  <PieChart>
                    <Pie
                      data={donutData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={50}
                      outerRadius={75}
                      paddingAngle={3}
                      stroke="none"
                    >
                      {donutData.map((d, i) => (
                        <Cell key={i} fill={d.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "var(--surface)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        color: "var(--ink)",
                        fontFamily: "var(--font-jetbrains-mono)",
                        fontSize: 12,
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="flex flex-wrap gap-4 mt-3">
                {donutData.map((d) => (
                  <div
                    key={d.name}
                    className="flex items-center gap-2 text-[12px] text-[var(--body)]"
                  >
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ background: d.color }}
                    />
                    <span>{d.name}</span>
                    <span className="mono text-[var(--ink)] font-semibold">
                      {formatNumber(d.value)}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-[13px] text-[var(--muted)] py-12 text-center">
              No events in the last 24 hours.
            </div>
          )}
        </div>
      </div>

      {/* Entity type breakdown */}
      {entityBars.length > 0 && (
        <div className="card p-5">
          <span className="eyebrow">Entity types</span>
          <h3 className="mt-1 mb-4 text-[15px] font-semibold text-[var(--ink)]">
            Top masked categories
          </h3>
          <div className="space-y-2.5">
            {entityBars.map((b) => {
              const max = entityBars[0].count || 1;
              const pct = Math.max(2, (b.count / max) * 100);
              return (
                <div key={b.type} className="flex items-center gap-3">
                  <span className="text-[12px] mono text-[var(--body)] w-32 truncate">
                    {b.type}
                  </span>
                  <div className="flex-1 h-1.5 bg-[var(--subtle)] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${pct}%`,
                        background: "var(--primary)",
                      }}
                    />
                  </div>
                  <span className="text-[12px] mono text-[var(--ink)] w-10 text-right">
                    {formatNumber(b.count)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent events */}
      <div className="card p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <span className="eyebrow">Recent</span>
            <h3 className="mt-1 text-[15px] font-semibold text-[var(--ink)]">
              Latest intercepted events
            </h3>
          </div>
          <a
            href="/audit"
            className="text-[12px] text-[var(--body)] hover:text-[var(--primary)] transition-colors"
          >
            View all →
          </a>
        </div>
        {recent.length === 0 ? (
          <div className="text-[13px] text-[var(--muted)] py-8 text-center">
            No events yet. Deploy a local backend to start seeing traffic.
          </div>
        ) : (
          <div className="space-y-1.5">
            {recent.slice(0, 6).map((e) => (
              <div
                key={e.id}
                className="flex items-center gap-3 py-2 border-b border-[var(--border)] last:border-b-0 text-[13px]"
              >
                <span
                  className={`status-dot ${
                    e.event_type === "fail_closed"
                      ? "danger"
                      : e.event_type === "mask"
                        ? ""
                        : "muted"
                  }`}
                />
                <span className="mono text-[var(--body)] text-[11px] w-24">
                  {formatTime(e.timestamp)}
                </span>
                <span
                  className="font-medium"
                  style={{
                    color:
                      e.event_type === "fail_closed"
                        ? "var(--danger)"
                        : "var(--ink)",
                  }}
                >
                  {e.event_type}
                </span>
                <span className="text-[var(--muted)] flex-1 truncate">
                  {e.entity_types
                    ? Object.entries(e.entity_types)
                        .map(([k, v]) => `${k} ×${v}`)
                        .join(" · ")
                    : "—"}
                </span>
                <span className="mono text-[11px] text-[var(--muted)]">
                  {e.latency_ms ? `${e.latency_ms}ms` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="max-w-[1100px] mx-auto">
      <div className="card p-10 text-center">
        <div className="inline-flex items-center justify-center h-12 w-12 rounded-full border border-[var(--border)] mb-4">
          <Ban className="h-5 w-5 text-[var(--danger)]" />
        </div>
        <h3 className="text-[18px] font-semibold serif text-[var(--ink)] mb-2">
          Can&apos;t reach the cloud backend
        </h3>
        <p className="text-[13px] text-[var(--body)] max-w-[420px] mx-auto mb-5">
          The dashboard needs the cloud backend running. Start it with{" "}
          <code className="mono text-[var(--ink)]">
            uvicorn app.main:app --port 8000
          </code>{" "}
          from the <code className="mono text-[var(--ink)]">cloud-backend/</code>{" "}
          folder.
        </p>
        <p className="text-[11px] mono text-[var(--muted)] mb-5">Error: {message}</p>
        <button className="btn-primary" onClick={onRetry}>
          Retry connection
        </button>
      </div>
    </div>
  );
}
