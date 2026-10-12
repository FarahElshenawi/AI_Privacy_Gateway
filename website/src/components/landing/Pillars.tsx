"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Eye, Lock, Replace, RotateCcw } from "lucide-react";
import { SectionHeader } from "./bits";

const STEPS = [
  { icon: Eye, title: "Intercept", desc: "Every prompt and every attached file is paused at the network layer, below the page's own scripts, before it reaches the AI tool." },
  { icon: Replace, title: "Mask", desc: "Sensitive values become realistic stand-ins or redaction tokens on the employee's own device. Checked a second time before sending." },
  { icon: RotateCcw, title: "Restore", desc: "The reply comes back with stand-ins; the real values are put back on screen. The user sees their data, the AI never did." },
];

function VisualIntercept() {
  return (
    <div className="space-y-3 text-[13px]">
      <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-black/30 px-3 py-2">
        <span className="mono-code text-[var(--body)]">POST /backend-api/f/conversation</span>
        <span className="flex items-center gap-1.5 rounded-full bg-[rgba(229,181,103,.12)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--amber)]"><Lock className="h-3 w-3" /> HELD</span>
      </div>
      <div className="mono-code rounded-lg border border-[var(--border)] bg-black/30 p-3 leading-[1.7] text-[var(--body)]">
        {"{ \"role\": \"user\", \"parts\": [\"Send the contract to "}
        <span className="tok tok-orig">Sarah Mitchell</span>{", card "}
        <span className="tok tok-orig">4532 0151 1283 0366</span>{"\"] }"}
      </div>
      <div className="grid grid-cols-3 gap-2 text-center text-[12px] text-[var(--body)]">
        {["Prompts", "Draft text", "Files"].map((t) => <div key={t} className="rounded-lg border border-[var(--border)] bg-white/[0.02] py-2">{t}</div>)}
      </div>
    </div>
  );
}

function VisualMask() {
  const rows = [
    { a: "Sarah Mitchell", b: "Emma Collins", cls: "tok-fake", tag: "stand-in, reversible" },
    { a: "sarah@acme-corp.com", b: "m.reyes@northwind.example", cls: "tok-fake", tag: "stand-in, reversible" },
    { a: "4532 0151 1283 0366", b: "[REDACTED:CREDIT_CARD]", cls: "tok-red", tag: "Luhn validated, redacted" },
  ];
  return (
    <div className="space-y-3">
      {rows.map((r, i) => (
        <motion.div key={r.a} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.18 }} className="rounded-lg border border-[var(--border)] bg-black/30 p-3 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="tok tok-orig">{r.a}</span>
            <ArrowRight className="h-3.5 w-3.5 text-[var(--muted)]" />
            <span className={`tok ${r.cls}`}>{r.b}</span>
          </div>
          <div className="mt-1.5 text-[11px] text-[var(--muted)]">{r.tag}</div>
        </motion.div>
      ))}
    </div>
  );
}

function VisualRestore() {
  const [real, setReal] = useState(false);
  useEffect(() => {
    const t = setInterval(() => setReal((v) => !v), 1900);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="space-y-3 text-[14px]">
      <div className="flex items-center justify-between text-[11px] uppercase tracking-[0.14em] text-[var(--muted)]">
        <span>AI reply</span>
        <span className={real ? "text-[var(--sky)]" : "text-[var(--primary)]"}>{real ? "what you see" : "what the AI wrote"}</span>
      </div>
      <div className="rounded-lg border border-[var(--border)] bg-black/30 p-4 leading-[1.9] text-[var(--ink)]">
        Hi{" "}
        <AnimatePresence mode="wait">
          <motion.span key={String(real)} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }} className={`tok ${real ? "tok-real" : "tok-fake"}`}>
            {real ? "Sarah Mitchell" : "Emma Collins"}
          </motion.span>
        </AnimatePresence>
        , your payment was received as <span className="tok tok-red">[REDACTED:CREDIT_CARD]</span>.
      </div>
      <p className="text-[12px] text-[var(--muted)]">Redacted values stay redacted. Only stand-ins are swapped back.</p>
    </div>
  );
}

const VISUALS = [VisualIntercept, VisualMask, VisualRestore];

export function Pillars() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const t = setTimeout(() => setActive((a) => (a + 1) % STEPS.length), 6000);
    return () => clearTimeout(t);
  }, [active, paused]);

  const Visual = VISUALS[active];

  return (
    <section id="how-it-works" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="How it works" title="Three steps. All on the employee's device." desc="Nothing sensitive crosses the network. The AI provider receives only stand-ins and placeholders." />

        <div className="mt-14 grid items-stretch gap-6 lg:grid-cols-[1fr_1.1fr]" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
          <div className="flex flex-col gap-3">
            {STEPS.map((s, i) => {
              const Icon = s.icon;
              const on = active === i;
              return (
                <button key={s.title} onClick={() => setActive(i)} className={`relative overflow-hidden rounded-2xl border p-5 text-left transition-colors ${on ? "border-[rgba(47,213,143,.5)] bg-[var(--surface)]" : "border-[var(--border)] bg-transparent hover:border-[var(--border-strong)]"}`}>
                  <div className="flex items-start gap-4">
                    <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl border transition-colors ${on ? "border-transparent bg-[var(--primary)] text-[var(--bg)]" : "border-[var(--border-strong)] text-[var(--body)]"}`}>
                      <Icon className="h-5 w-5" strokeWidth={2.25} />
                    </span>
                    <div>
                      <div className="flex items-baseline gap-3">
                        <span className="mono-code text-[12px] text-[var(--muted)]">0{i + 1}</span>
                        <h3 className="serif text-[22px] text-[var(--ink)]">{s.title}</h3>
                      </div>
                      <AnimatePresence initial={false}>
                        {on && (
                          <motion.p initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.3 }} className="overflow-hidden pt-2 text-[15px] leading-[1.65] text-[var(--body)]">
                            {s.desc}
                          </motion.p>
                        )}
                      </AnimatePresence>
                    </div>
                  </div>
                  {on && !paused && <motion.span key={`bar-${active}`} className="absolute bottom-0 left-0 h-[2px] bg-[var(--primary)]" initial={{ width: 0 }} animate={{ width: "100%" }} transition={{ duration: 6, ease: "linear" }} />}
                </button>
              );
            })}
          </div>

          <div className="panel flex min-h-[330px] flex-col justify-center p-6 lg:p-8">
            <AnimatePresence mode="wait">
              <motion.div key={active} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -14 }} transition={{ duration: 0.3 }}>
                <Visual />
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </section>
  );
}
