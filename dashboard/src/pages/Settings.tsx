import { useEffect, useState } from "react";
import { api, getApiKey, setApiKey } from "../api/client";
import { Server, Key, FileSignature } from "lucide-react";
import { AdminLogCard, DetectionCard, DevicesCard, EnrollKeyCard } from "../components/FleetCards";

export function Settings() {
  const [health, setHealth] = useState<string | null>(null);
  const [exportedPolicies, setExportedPolicies] = useState<Record<string, string> | null>(null);
  const [keyInput, setKeyInput] = useState(getApiKey());

  const loadPolicies = () =>
    api
      .exportPolicies(1)
      .then(setExportedPolicies)
      .catch(() => setExportedPolicies(null));

  useEffect(() => {
    api
      .health()
      .then((h) => setHealth(`${h.status} · ${h.version}`))
      .catch(() => setHealth("offline"));
    loadPolicies();
  }, []);

  return (
    <div className="max-w-[800px] mx-auto space-y-8">
      <div>
        <span className="eyebrow">Settings</span>
        <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
          Organization
        </h1>
        <p className="mt-1 text-[14px] text-[var(--body)]">
          Devices, detection settings and keys for your organization.
        </p>
      </div>

      <DevicesCard />
      <DetectionCard />
      <EnrollKeyCard />

      {/* Connection card */}
      <div className="card p-6">
        <div className="flex items-center gap-2 mb-4">
          <Server className="h-4 w-4 text-[var(--primary)]" />
          <span className="text-[14px] font-semibold text-[var(--ink)]">
            Cloud backend connection
          </span>
        </div>
        <div className="space-y-3 text-[13px]">
          <Row label="URL">
            <span className="mono text-[var(--ink)]">
              {(import.meta as any).env?.VITE_CLOUD_BACKEND_URL || "/api"}
            </span>
          </Row>
          <Row label="Health">
            {health ? (
              <span className="flex items-center gap-2">
                <span className="status-dot" />
                <span className="mono text-[var(--ink)]">{health}</span>
              </span>
            ) : (
              <span className="text-[var(--muted)]">checking…</span>
            )}
          </Row>
          <Row label="Change URL">
            <span className="text-[var(--body)]">
              Set <code className="mono text-[var(--ink)]">VITE_CLOUD_BACKEND_URL</code>{" "}
              in <code className="mono text-[var(--ink)]">.env</code>, then restart Vite.
            </span>
          </Row>
        </div>
      </div>

      {/* API key card */}
      <div className="card p-6">
        <div className="flex items-center gap-2 mb-4">
          <Key className="h-4 w-4 text-[var(--primary)]" />
          <span className="text-[14px] font-semibold text-[var(--ink)]">
            Per-org API key
          </span>
        </div>
        <p className="text-[13px] text-[var(--body)] mb-3">
          Every dashboard request is authenticated with your organization&apos;s
          API key. It is kept for this browser tab only (cleared when the tab
          closes). Create or rotate keys with
          <code className="mono text-[var(--ink)]"> python -m app.admin create-org</code> on the
          cloud backend.
        </p>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setApiKey(keyInput);
            loadPolicies();
          }}
        >
          <input
            type="password"
            autoComplete="off"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="Organization API key"
            className="flex-1 mono text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded-lg p-3"
          />
          <button type="submit" className="btn-primary px-4 rounded-lg text-[13px]">Save</button>
        </form>
      </div>

      <AdminLogCard />

      {/* Exported policy snapshot */}
      {exportedPolicies && (
        <div className="card p-6">
          <div className="flex items-center gap-2 mb-4">
            <FileSignature className="h-4 w-4 text-[var(--primary)]" />
            <span className="text-[14px] font-semibold text-[var(--ink)]">
              Exported policy snapshot
            </span>
          </div>
          <p className="text-[13px] text-[var(--body)] mb-3">
            The current policy set as it would be served to a local backend
            pulling from <code className="mono text-[var(--ink)]">/api/policies/export</code>.
          </p>
          <code className="block mono text-[12px] text-[var(--body)] bg-[var(--bg)] border border-[var(--border)] rounded-lg p-3 overflow-x-auto max-h-[260px] overflow-y-auto">
            {JSON.stringify(exportedPolicies, null, 2)}
          </code>
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4 py-1.5">
      <div className="eyebrow w-28 flex-shrink-0 pt-0.5">{label}</div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
