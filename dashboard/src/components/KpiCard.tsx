import type { ReactNode } from "react";

export function KpiCard({
  label,
  value,
  sub,
  tone = "default",
  icon,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: "default" | "primary" | "danger";
  icon?: ReactNode;
}) {
  const color =
    tone === "primary"
      ? "var(--primary)"
      : tone === "danger"
        ? "var(--danger)"
        : "var(--ink)";
  return (
    <div className="card p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="eyebrow">{label}</span>
        {icon && (
          <span style={{ color: tone === "default" ? "var(--muted)" : color }}>
            {icon}
          </span>
        )}
      </div>
      <div
        className="text-[32px] font-semibold serif tracking-tight leading-none"
        style={{ color }}
      >
        {value}
      </div>
      {sub && <div className="text-[12.5px] text-[var(--muted)]">{sub}</div>}
    </div>
  );
}
