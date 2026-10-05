import { NavLink } from "react-router-dom";
import { ShieldCheck, LayoutDashboard, Sliders, ScrollText, Settings } from "lucide-react";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/policies", label: "Policies", icon: Sliders, end: false },
  { to: "/audit", label: "Audit log", icon: ScrollText, end: false },
  { to: "/settings", label: "Settings", icon: Settings, end: false },
];

export function Sidebar() {
  return (
    <aside className="hidden lg:flex w-64 flex-col border-r border-[var(--border)] bg-[var(--bg)] flex-shrink-0">
      <div className="px-5 h-16 flex items-center gap-2.5 border-b border-[var(--border)]">
        <span
          className="grid place-items-center h-9 w-9 rounded-xl"
          style={{ background: "var(--primary)", border: "1px solid var(--primary)" }}
        >
          <ShieldCheck className="h-5 w-5 text-[var(--bg)]" strokeWidth={2.5} />
        </span>
        <div className="flex flex-col">
          <span className="text-[15px] font-medium tracking-tight serif text-[var(--ink)]">
            Doppel
          </span>
          <span className="text-[10px] text-[var(--muted)] mono uppercase tracking-[0.18em]">
            Control plane
          </span>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 flex flex-col gap-1">
        {NAV.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
            >
              <Icon className="nav-icon h-4.5 w-4.5" strokeWidth={2.25} />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-[var(--border)]">
        <div className="text-[11px] mono uppercase tracking-[0.18em] text-[var(--muted)] mb-2">
          Status
        </div>
        <div className="flex items-center gap-2 text-[13px] text-[var(--body)]">
          <span className="status-dot" />
          <span>Cloud backend online</span>
        </div>
      </div>
    </aside>
  );
}
