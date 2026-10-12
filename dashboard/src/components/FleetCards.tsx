import { useEffect, useState } from "react";
import { Laptop, ScrollText, SlidersHorizontal, KeyRound } from "lucide-react";
import { api, type AdminAction, type EndpointInfo, type ImagePolicy } from "../api/client";
import { formatRelative, parseUtc } from "../lib/utils";

const STALE_MS = 5 * 60 * 1000;     // the cloud treats a device silent for 5 minutes as stale
const msg = (e: unknown) => (e instanceof Error ? e.message : "Request failed");

export function DevicesCard() {
  const [rows, setRows] = useState<EndpointInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.listEndpoints().then((r) => { setRows(r); setError(null); }).catch((e) => setError(msg(e)));
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const remove = async (e: EndpointInfo) => {
    if (!window.confirm(`Deactivate ${e.hostname ?? "this device"}? Its audit history is kept.`)) return;
    try { await api.deactivateEndpoint(e.id); await load(); } catch (err) { setError(msg(err)); }
  };

  const active = (rows ?? []).filter((r) => r.is_active);
  const stale = (r: EndpointInfo) => !r.last_seen || Date.now() - parseUtc(r.last_seen).getTime() > STALE_MS;
  const versions = new Set(active.map((r) => r.version).filter(Boolean));

  return (
    <div className="card p-6">
      <div className="flex items-center gap-2 mb-1">
        <Laptop className="h-4 w-4 text-[var(--primary)]" />
        <span className="text-[14px] font-semibold text-[var(--ink)]">Devices</span>
      </div>
      <p className="text-[13px] text-[var(--body)] mb-4">
        {rows === null ? "Loading…" : `${active.filter((r) => !stale(r)).length} online · ${active.filter(stale).length} not seen in 5 min`}
        {versions.size > 1 && <span className="text-amber-400"> · {versions.size} different versions running</span>}
      </p>
      {error && <div className="text-[12px] text-[var(--danger)] mb-3">{error}</div>}
      {rows && rows.length === 0 && (
        <div className="text-[13px] text-[var(--muted)]">No devices enrolled yet. Install the local backend with the enrollment key.</div>
      )}
      <div className="divide-y divide-[var(--border)]">
        {(rows ?? []).map((r) => (
          <div key={r.id} className="flex items-center gap-3 py-2.5 text-[13px]">
            <span className="status-dot" style={{ opacity: r.is_active && !stale(r) ? 1 : 0.35 }} />
            <span className="mono text-[var(--ink)] flex-1 min-w-0 truncate">{r.hostname ?? `device ${r.id}`}</span>
            <span className="mono text-[var(--muted)] text-[12px]">{r.version ? `v${r.version}` : "—"}</span>
            <span className="text-[var(--muted)] text-[12px] w-24 text-right">{r.last_seen ? formatRelative(r.last_seen) : "never"}</span>
            {r.is_active ? (
              <button className="btn-ghost px-2 py-1 text-[12px]" onClick={() => remove(r)}>Deactivate</button>
            ) : (
              <span className="text-[11px] uppercase tracking-wider text-[var(--muted)] w-[78px] text-right">inactive</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function EnrollKeyCard() {
  const [key, setKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rotate = async () => {
    if (!window.confirm("Issue a new enrollment key? The old one stops working for NEW devices; enrolled devices keep working.")) return;
    try { setKey((await api.rotateEnrollKey()).enroll_key); setError(null); } catch (e) { setError(msg(e)); }
  };
  return (
    <div className="card p-6">
      <div className="flex items-center gap-2 mb-1">
        <KeyRound className="h-4 w-4 text-[var(--primary)]" />
        <span className="text-[14px] font-semibold text-[var(--ink)]">Enrollment key</span>
      </div>
      <p className="text-[13px] text-[var(--body)] mb-3">
        New devices enroll with this key, which can do nothing else. Rotate it if it leaked or after a rollout.
      </p>
      <button className="btn-primary px-4 py-2 rounded-lg text-[13px]" onClick={rotate}>Rotate enrollment key</button>
      {error && <div className="text-[12px] text-[var(--danger)] mt-3">{error}</div>}
      {key && (
        <div className="mt-4">
          <div className="eyebrow mb-1">New key (shown once, copy it now)</div>
          <code className="block mono text-[12px] text-[var(--ink)] bg-[var(--bg)] border border-[var(--border)] rounded-lg p-3 break-all select-all">{key}</code>
        </div>
      )}
    </div>
  );
}

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export function DetectionCard() {
  const [terms, setTerms] = useState("");
  const [domains, setDomains] = useState("");
  const [images, setImages] = useState<ImagePolicy>("default");
  const [version, setVersion] = useState<number | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api.getTenantConfig().then((c) => {
      setTerms(c.deny_terms.join("\n")); setDomains(c.tenant_domains.join("\n"));
      setImages(c.image_policy); setVersion(c.version);
    }).catch((e) => setNote({ ok: false, text: msg(e) }));
  }, []);

  const save = async (ev: React.FormEvent) => {
    ev.preventDefault();
    try {
      const c = await api.putTenantConfig({ deny_terms: lines(terms), tenant_domains: lines(domains), image_policy: images });
      setTerms(c.deny_terms.join("\n")); setDomains(c.tenant_domains.join("\n")); setVersion(c.version);
      setNote({ ok: true, text: `Saved (version ${c.version}). Devices pick it up on their next sync.` });
    } catch (e) { setNote({ ok: false, text: msg(e) }); }
  };

  const box = "w-full mono text-[12px] bg-[var(--bg)] border border-[var(--border)] rounded-lg p-3";
  return (
    <form className="card p-6" onSubmit={save}>
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal className="h-4 w-4 text-[var(--primary)]" />
        <span className="text-[14px] font-semibold text-[var(--ink)]">Detection settings</span>
        {version !== null && <span className="mono text-[11px] text-[var(--muted)] ml-auto">v{version}</span>}
      </div>
      <p className="text-[13px] text-[var(--body)] mb-4">Applied to every enrolled device. One entry per line.</p>

      <label className="eyebrow block mb-1">Deny terms (client and project names, always masked)</label>
      <textarea className={box} rows={4} value={terms} onChange={(e) => setTerms(e.target.value)} placeholder={"Project Falcon\nAcme Corp"} />
      <p className="text-[11.5px] text-[var(--muted)] mt-1 mb-4">Not written to the admin log. Only the count is recorded.</p>

      <label className="eyebrow block mb-1">Internal domains (hostnames under these are masked)</label>
      <textarea className={box} rows={3} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="corp.acme.com" />

      <label className="eyebrow block mt-4 mb-1">Images inside files</label>
      <select className={box} value={images} onChange={(e) => setImages(e.target.value as ImagePolicy)}>
        <option value="default">Default: standalone images blocked, images inside documents allowed with a warning</option>
        <option value="block">Strict: any file containing an image is blocked</option>
        <option value="warn">Permissive: images allowed and sent unchanged, with a warning</option>
      </select>
      <p className="text-[11.5px] text-[var(--muted)] mt-1">Images are not read, so anything shown in them is not masked.</p>

      <div className="flex items-center gap-3 mt-5">
        <button type="submit" className="btn-primary px-4 py-2 rounded-lg text-[13px]">Save</button>
        {note && <span className={`text-[12px] ${note.ok ? "text-[var(--body)]" : "text-[var(--danger)]"}`}>{note.text}</span>}
      </div>
    </form>
  );
}

export function AdminLogCard() {
  const [rows, setRows] = useState<AdminAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.adminLog(30).then(setRows).catch((e) => setError(msg(e))); }, []);
  return (
    <div className="card p-6">
      <div className="flex items-center gap-2 mb-4">
        <ScrollText className="h-4 w-4 text-[var(--primary)]" />
        <span className="text-[14px] font-semibold text-[var(--ink)]">Admin log</span>
      </div>
      {error && <div className="text-[12px] text-[var(--danger)]">{error}</div>}
      {rows && rows.length === 0 && <div className="text-[13px] text-[var(--muted)]">Nothing yet.</div>}
      <div className="divide-y divide-[var(--border)]">
        {(rows ?? []).map((r) => (
          <div key={r.id} className="flex items-baseline gap-3 py-2 text-[12.5px]">
            <span className="mono text-[var(--ink)]">{r.action}</span>
            <span className="mono text-[var(--muted)] flex-1 min-w-0 truncate">{r.target ?? ""}</span>
            <span className="text-[var(--muted)]">{formatRelative(r.timestamp)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
