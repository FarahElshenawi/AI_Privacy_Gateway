import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ShieldCheck, Server, Key, FileSignature } from "lucide-react";

export function Settings() {
  const [health, setHealth] = useState<string | null>(null);
  const [exportedPolicies, setExportedPolicies] = useState<Record<string, string> | null>(null);
  const [signedPolicy, setSignedPolicy] = useState(true);

  useEffect(() => {
    api
      .health()
      .then((h) => setHealth(`${h.status} · ${h.version}`))
      .catch(() => setHealth("offline"));
    api
      .exportPolicies(1)
      .then(setExportedPolicies)
      .catch(() => setExportedPolicies(null));
  }, []);

  return (
    <div className="max-w-[800px] mx-auto space-y-8">
      <div>
        <span className="eyebrow">Settings</span>
        <h1 className="mt-2 text-[32px] font-semibold serif tracking-tight text-[var(--ink)]">
          Organization
        </h1>
        <p className="mt-1 text-[14px] text-[var(--body)]">
          Read-only organization info. Edit on the cloud backend directly.
        </p>
      </div>

      {/* Org card */}
      <div className="card p-6">
        <div className="flex items-start gap-4">
          <span
            className="grid place-items-center h-12 w-12 rounded-xl flex-shrink-0"
            style={{
              background: "var(--primary)",
              border: "1px solid var(--primary)",
            }}
          >
            <ShieldCheck className="h-6 w-6 text-[var(--bg)]" strokeWidth={2.25} />
          </span>
          <div className="flex-1">
            <div className="text-[18px] font-semibold serif text-[var(--ink)]">
              Acme Inc.
            </div>
            <div className="text-[13px] text-[var(--body)] mt-1">
              The default organization enrolled in this cloud control plane.
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4 text-[13px]">
              <div>
                <div className="eyebrow mb-1">Org ID</div>
                <div className="mono text-[var(--ink)]">1</div>
              </div>
              <div>
                <div className="eyebrow mb-1">Endpoints enrolled</div>
                <div className="mono text-[var(--ink)]">1 (this dashboard)</div>
              </div>
            </div>
          </div>
        </div>
      </div>

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
          The cloud backend uses this key to authenticate the local backends
          that enroll under this org. Stored in the cloud backend&apos;s
          <code className="mono text-[var(--ink)]"> organizations</code> table —
          rotate via the cloud backend CLI.
        </p>
        <code className="block mono text-[12px] text-[var(--body)] bg-[var(--bg)] border border-[var(--border)] rounded-lg p-3 break-all">
          sk-doppel-••••••••••••••••••••••••••••••••
        </code>
        <p className="text-[11px] text-[var(--muted)] mt-2">
          Hidden by default — visible only to org admins.
        </p>
      </div>

      {/* Signed policy toggle */}
      <div className="card p-6">
        <div className="flex items-center gap-2 mb-4">
          <FileSignature className="h-4 w-4 text-[var(--primary)]" />
          <span className="text-[14px] font-semibold text-[var(--ink)]">
            Signed policy distribution
          </span>
        </div>
        <p className="text-[13px] text-[var(--body)] mb-4">
          When enabled, every policy update is signed by the cloud backend&apos;s
          signing key. The local backend rejects unsigned policies. Recommended
          for production deployments.
        </p>
        <label className="flex items-center gap-3 cursor-pointer">
          <button
            type="button"
            role="switch"
            aria-checked={signedPolicy}
            onClick={() => setSignedPolicy((v) => !v)}
            className="relative h-6 w-11 rounded-full transition-colors"
            style={{
              background: signedPolicy ? "var(--primary)" : "var(--subtle)",
              border: "1px solid var(--border)",
            }}
          >
            <span
              className="absolute top-0.5 h-4 w-4 rounded-full bg-[var(--bg)] transition-transform"
              style={{ left: signedPolicy ? "calc(100% - 18px)" : "2px" }}
            />
          </button>
          <span className="text-[13px] text-[var(--ink)]">
            {signedPolicy ? "Enabled — policies are signed" : "Disabled — policies are unsigned"}
          </span>
        </label>
      </div>

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
