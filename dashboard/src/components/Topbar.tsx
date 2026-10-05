import { useEffect, useState } from "react";
import { api } from "../api/client";

export function Topbar() {
  const [status, setStatus] = useState<"online" | "offline">("offline");
  const [backendUrl, setBackendUrl] = useState("");

  useEffect(() => {
    const url =
      (import.meta as any).env?.VITE_CLOUD_BACKEND_URL || "/api";
    setBackendUrl(url);
    let cancelled = false;
    const check = () => {
      api
        .health()
        .then(() => !cancelled && setStatus("online"))
        .catch(() => !cancelled && setStatus("offline"));
    };
    check();
    const interval = setInterval(check, 10000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <header className="h-16 px-6 flex items-center justify-between border-b border-[var(--border)] bg-[var(--bg)] flex-shrink-0">
      <div className="flex items-center gap-3">
        <span
          className={`status-dot ${
            status === "online" ? "" : status === "offline" ? "muted" : "muted"
          }`}
        />
        <span className="text-[13px] text-[var(--body)]">
          {status === "online" ? "Connected to cloud backend" : "Cloud backend offline"}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-[12px] mono text-[var(--muted)] hidden sm:inline">
          {backendUrl}
        </span>
        <span className="text-[13px] text-[var(--ink)] font-medium">Acme Inc.</span>
      </div>
    </header>
  );
}
