"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, Ban, Clock, LayoutDashboard, ShieldCheck, Sliders } from "lucide-react";
import { SectionHeader } from "./bits";

type Ev = { id: number; time: string; type: "mask" | "file" | "fail_closed" | "demask"; types: Record<string, number>; ms: number | null };

const POOL: Omit<Ev, "id" | "time">[] = [
  { type: "mask", types: { PERSON: 2, EMAIL: 1 }, ms: 41 },
  { type: "file", types: { US_SSN: 1, PERSON: 3 }, ms: 312 },
  { type: "mask", types: { API_KEY: 1 }, ms: 18 },
  { type: "mask", types: { CREDIT_CARD: 1, CVV: 1, CARD_EXPIRY: 1 }, ms: 22 },
  { type: "fail_closed", types: { DEGRADED_STRICT: 1 }, ms: null },
  { type: "demask", types: {}, ms: null },
  { type: "mask", types: { IBAN: 1, PERSON: 1 }, ms: 36 },
];

const POLICIES = [
  { t: "PERSON", a: "faker", guard: false },
  { t: "EMAIL", a: "faker", guard: false },
  { t: "ORGANIZATION", a: "faker", guard: false },
  { t: "CREDIT_CARD", a: "redact", guard: true },
  { t: "API_KEY", a: "redact", guard: true },
  { t: "INTERNAL_URL", a: "redact", guard: false },
];
const ACTS = ["faker", "redact", "keep", "block"];
const danger = new Set(["API_KEY", "CREDIT_CARD", "US_SSN", "IBAN", "CVV"]);
const stamp = () => new Date().toLocaleTimeString("en-GB");
const spark = [8, 12, 9, 15, 13, 19, 16, 22, 18, 25, 21, 28];

export function DashboardPreview() {
  const [view, setView] = useState<"overview" | "policies">("overview");
  const [events, setEvents] = useState<Ev[]>([]);
  const [stats, setStats] = useState({ total: 1284, masked: 3917, closed: 12 });
  const [pol, setPol] = useState(POLICIES);
  const [msg, setMsg] = useState("");
  const n = useRef(0);

  useEffect(() => {
    const add = () => {
      const p = POOL[n.current % POOL.length];
      const ev: Ev = { ...p, id: n.current, time: stamp() };
      n.current++;
      setEvents((e) => [ev, ...e].slice(0, 5));
      setStats((s) => ({
        total: s.total + 1,
        masked: s.masked + Object.values(p.types).reduce((a, b) => a + b, 0),
        closed: s.closed + (p.type === "fail_closed" ? 1 : 0),
      }));
    };
    for (let i = 0; i < 4; i++) add();
    const t = setInterval(add, 2300);
    return () => clearInterval(t);
  }, []);

  const setAction = (i: number, a: string) => {
    if (a === "keep" && pol[i].guard) { setMsg(`Security guard: ${pol[i].t} cannot be set to keep.`); return; }
    setPol((p) => p.map((x, k) => (k === i ? { ...x, a } : x)));
    setMsg(`${pol[i].t} → ${a}. Devices pull the change within 5 minutes.`);
  };

  const max = Math.max(...spark);
  const path = spark.map((v, i) => `${i === 0 ? "M" : "L"}${(i / (spark.length - 1)) * 300},${70 - (v / max) * 60}`).join(" ");

  return (
    <section id="platform" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="Platform" title="A control plane your security team will actually use." desc="Set policy once, push it to every device, and watch what was masked in real time. Metadata only: types, counts and timing." />

        <div className="panel mt-14 overflow-hidden rounded-[20px] shadow-[0_40px_120px_-40px_rgba(47,213,143,.2)]">
          <div className="grid lg:grid-cols-[210px_1fr]">
            <aside className="hidden flex-col border-r border-[var(--border)] bg-black/20 p-4 lg:flex">
              <div className="mb-6 flex items-center gap-2.5 px-2">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--primary)]"><ShieldCheck className="h-4 w-4 text-[var(--bg)]" strokeWidth={2.5} /></span>
                <div className="leading-tight"><div className="serif text-[15px]">Doppel</div><div className="text-[9px] uppercase tracking-[0.18em] text-[var(--muted)]">Control plane</div></div>
              </div>
              {([["overview", "Overview", LayoutDashboard], ["policies", "Policies", Sliders]] as const).map(([id, l, Icon]) => (
                <button key={id} onClick={() => setView(id)} className={`mb-1 flex items-center gap-3 rounded-lg px-3 py-2.5 text-[14px] transition-colors ${view === id ? "bg-white/[0.06] text-[var(--ink)]" : "text-[var(--body)] hover:text-[var(--ink)]"}`}>
                  <Icon className={`h-4 w-4 ${view === id ? "text-[var(--primary)]" : ""}`} /> {l}
                </button>
              ))}
              <div className="mt-auto flex items-center gap-2 px-2 pt-6 text-[12px] text-[var(--body)]"><span className="pulse-dot" /> Cloud backend online</div>
            </aside>

            <div className="p-5 lg:p-7">
              <div className="mb-5 flex items-center justify-between">
                <div className="flex gap-2 lg:hidden">
                  {(["overview", "policies"] as const).map((v) => (
                    <button key={v} onClick={() => setView(v)} className={`rounded-full border px-3 py-1 text-[12px] capitalize ${view === v ? "border-[var(--primary)] text-[var(--primary)]" : "border-[var(--border-strong)] text-[var(--body)]"}`}>{v}</button>
                  ))}
                </div>
                <h3 className="serif hidden text-[24px] lg:block">{view === "overview" ? "Last 24 hours" : "Deterministic routing rules"}</h3>
                <span className="rounded-full border border-[var(--border-strong)] px-3 py-1 text-[11px] text-[var(--muted)]">Sample data</span>
              </div>

              {view === "overview" ? (
                <>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {[
                      { l: "Total events", v: stats.total.toLocaleString(), i: Activity, c: "var(--ink)" },
                      { l: "Entities masked", v: stats.masked.toLocaleString(), i: ShieldCheck, c: "var(--primary)" },
                      { l: "Fail-closed", v: String(stats.closed), i: Ban, c: "var(--danger)" },
                      { l: "Avg latency", v: "47 ms", i: Clock, c: "var(--ink)" },
                    ].map((k) => (
                      <div key={k.l} className="rounded-xl border border-[var(--border)] bg-black/25 p-4">
                        <div className="flex items-center justify-between text-[11px] uppercase tracking-[0.14em] text-[var(--muted)]">{k.l}<k.i className="h-3.5 w-3.5" /></div>
                        <div className="serif mt-2 text-[28px] tabular-nums" style={{ color: k.c }}>{k.v}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-3 grid gap-3 lg:grid-cols-[1fr_1.3fr]">
                    <div className="rounded-xl border border-[var(--border)] bg-black/25 p-4">
                      <div className="text-[12px] text-[var(--muted)]">Masked events per hour</div>
                      <svg viewBox="0 0 300 80" className="mt-3 h-24 w-full overflow-visible">
                        <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#2fd58f" stopOpacity=".35" /><stop offset="1" stopColor="#2fd58f" stopOpacity="0" /></linearGradient></defs>
                        <path d={`${path} L300,80 L0,80 Z`} fill="url(#g)" />
                        <motion.path d={path} fill="none" stroke="#2fd58f" strokeWidth="2" strokeLinecap="round" initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.6, ease: "easeOut" }} />
                      </svg>
                    </div>
                    <div className="rounded-xl border border-[var(--border)] bg-black/25 p-4">
                      <div className="mb-2 flex items-center justify-between text-[12px] text-[var(--muted)]"><span>Intercepted events</span><span className="flex items-center gap-1.5"><span className="pulse-dot !h-1.5 !w-1.5" /> Live</span></div>
                      <div className="min-h-[150px] space-y-1.5">
                        <AnimatePresence initial={false}>
                          {events.map((e) => (
                            <motion.div key={e.id} layout initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-3 border-b border-[var(--border)] py-1.5 text-[12.5px] last:border-0">
                              <span className="mono-code w-[62px] text-[11px] text-[var(--muted)]">{e.time}</span>
                              <span className={`mono-code w-[78px] font-semibold ${e.type === "fail_closed" ? "text-[var(--danger)]" : "text-[var(--ink)]"}`}>{e.type}</span>
                              <span className="flex flex-1 flex-wrap gap-1">
                                {Object.entries(e.types).map(([k, v]) => <span key={k} className={`mono-code rounded border px-1.5 text-[10.5px] ${danger.has(k) ? "border-[rgba(224,96,91,.35)] text-[var(--danger)]" : "border-[rgba(47,213,143,.3)] text-[var(--primary)]"}`}>{k} ×{v}</span>)}
                              </span>
                              <span className="mono-code text-[11px] text-[var(--muted)]">{e.ms ? `${e.ms}ms` : ""}</span>
                            </motion.div>
                          ))}
                        </AnimatePresence>
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div>
                  <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-black/25">
                    {pol.map((p, i) => (
                      <div key={p.t} className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3 last:border-0">
                        <div className="flex items-center gap-2.5">
                          <span className="mono-code text-[13px]">{p.t}</span>
                          {p.guard && <span className="flex items-center gap-1 rounded bg-[rgba(229,181,103,.1)] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-[var(--amber)]"><ShieldCheck className="h-3 w-3" /> guarded</span>}
                        </div>
                        <div className="flex gap-1.5">
                          {ACTS.map((a) => (
                            <button key={a} onClick={() => setAction(i, a)} className={`rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition-colors ${p.a === a ? (a === "redact" ? "border-[rgba(224,96,91,.5)] bg-[rgba(224,96,91,.12)] text-[var(--danger)]" : a === "faker" ? "border-[rgba(47,213,143,.5)] bg-[rgba(47,213,143,.12)] text-[var(--primary)]" : "border-[var(--border-strong)] bg-white/[0.06] text-[var(--ink)]") : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--ink)]"} ${a === "keep" && p.guard ? "opacity-30" : ""}`}>{a}</button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-3 min-h-[20px] text-[13px] text-[var(--body)]">{msg || "Click an action to try it. Changes are local to this demo."}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
