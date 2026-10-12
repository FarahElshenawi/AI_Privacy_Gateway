"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bot, Eye, Send } from "lucide-react";

type Action = "faker" | "redact";
type Ent = { start: number; end: number; label: string; action: Action; real: string; shown: string };
type Seg = { text: string; ent?: Ent };

function luhn(d: string) {
  if (d.length < 13 || d.length > 19) return false;
  let sum = 0;
  for (let i = 0; i < d.length; i++) {
    let n = Number(d[d.length - 1 - i]);
    if (i % 2 === 1) { n *= 2; if (n > 9) n -= 9; }
    sum += n;
  }
  return sum % 10 === 0;
}

const RULES: { label: string; re: RegExp; action: Action; group?: number; ok?: (s: string) => boolean }[] = [
  { label: "CONNECTION_STRING", re: /\b(?:postgres(?:ql)?|mysql|mongodb|redis):\/\/[^\s"']+/g, action: "redact" },
  { label: "API_KEY", re: /\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b/g, action: "redact" },
  { label: "EMAIL", re: /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g, action: "faker" },
  { label: "CREDIT_CARD", re: /\b\d(?:[ -]?\d){12,18}\b/g, action: "redact", ok: (s) => luhn(s.replace(/\D/g, "")) },
  { label: "US_SSN", re: /\b\d{3}-\d{2}-\d{4}\b/g, action: "redact" },
  { label: "IBAN", re: /\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b/g, action: "redact" },
  { label: "PHONE_NUMBER", re: /(?:phone|tel|call|mobile)\b[:\s]*(\+?\d[\d\s().-]{6,16}\d)/gi, action: "faker", group: 1 },
  { label: "PERSON", re: /\b(?:Sarah|John|Emma|Omar|Farah|Mohamed|Ahmed|Layla|Michael|David|Anna|Sofia|Daniel)\s+[A-Z][a-z]+\b/g, action: "faker" },
];

const FAKES: Record<string, string[]> = {
  PERSON: ["Emma Collins", "Liam Parker", "Nora Bennett", "Jake Morton", "Ava Brooks"],
  EMAIL: ["m.reyes@northwind.example", "j.carter@fabrikam.example", "a.novak@contoso.example"],
  PHONE_NUMBER: ["+1 415 555 0132", "+44 20 7946 0958", "+49 30 5550 1470"],
};

function detect(text: string): Ent[] {
  const raw: Omit<Ent, "shown">[] = [];
  for (const r of RULES) {
    for (const m of text.matchAll(r.re)) {
      const val = r.group ? m[r.group] : m[0];
      if (!val) continue;
      const start = (m.index ?? 0) + (r.group ? m[0].indexOf(val) : 0);
      if (r.ok && !r.ok(val)) continue;
      raw.push({ start, end: start + val.length, label: r.label, action: r.action, real: val });
    }
  }
  raw.sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const kept: Omit<Ent, "shown">[] = [];
  let edge = -1;
  for (const e of raw) { if (e.start >= edge) { kept.push(e); edge = e.end; } }
  const vault = new Map<string, string>();
  const used: Record<string, number> = {};
  return kept.map((e) => {
    if (e.action === "redact") return { ...e, shown: `[REDACTED:${e.label}]` };
    const key = `${e.label}|${e.real.toLowerCase()}`;
    if (!vault.has(key)) {
      const pool = FAKES[e.label] ?? ["Redacted Value"];
      const i = used[e.label] ?? 0;
      used[e.label] = i + 1;
      vault.set(key, pool[i % pool.length]);
    }
    return { ...e, shown: vault.get(key)! };
  });
}

function segments(text: string, ents: Ent[]): Seg[] {
  const out: Seg[] = [];
  let pos = 0;
  for (const e of ents) {
    if (e.start > pos) out.push({ text: text.slice(pos, e.start) });
    out.push({ text: e.real, ent: e });
    pos = e.end;
  }
  if (pos < text.length) out.push({ text: text.slice(pos) });
  return out;
}

type Part = { t: string; ent?: Ent };
function buildReply(ents: Ent[]): Part[] {
  const person = ents.find((e) => e.label === "PERSON");
  const email = ents.find((e) => e.label === "EMAIL");
  const red = ents.find((e) => e.action === "redact");
  if (!ents.length) return [{ t: "No sensitive data detected in this prompt, so nothing was changed. Try adding a name, an email or a card number." }];
  const p: Part[] = [{ t: "Hi " }];
  if (person) p.push({ t: "", ent: person }, { t: ", " }); else p.push({ t: "there, " });
  p.push({ t: "thanks for the details. " });
  if (email) p.push({ t: "I'll send the confirmation to " }, { t: "", ent: email }, { t: ". " });
  if (red) p.push({ t: "The " + red.label.toLowerCase().replace(/_/g, " ") + " reached me only as " }, { t: "", ent: red }, { t: ", so nothing sensitive was exposed." });
  return p;
}

const SAMPLES = [
  { name: "Support ticket", text: "Hi, I'm Sarah Mitchell. Please send the contract to sarah.mitchell@acme-corp.com and charge card 4532 0151 1283 0366. My SSN is 123-45-6789." },
  { name: "Dev incident", text: "Deploy failed for Omar Hassan. Connection: postgresql://app:Zx81kLm3@db.internal:5432/prod and key sk-proj-4f9Kd82LmQ7xVb3NpTz81Wc. Please debug." },
  { name: "Finance", text: "Wire the deposit to IBAN DE89 3704 0044 0532 0130 00 for Layla Ahmed. Call +49 151 23456789 if there is any issue." },
];

const STAGES = [
  { id: "ai", label: "AI receives", icon: Send, note: "This is all the AI provider ever sees." },
  { id: "reply", label: "AI replies", icon: Bot, note: "The model answers using the stand-ins." },
  { id: "you", label: "You see", icon: Eye, note: "Real values restored on your screen only." },
];

function Tok({ ent, mode }: { ent: Ent; mode: "orig" | "ai" | "real" }) {
  if (mode === "orig") return <mark className="tok tok-orig" title={ent.label}>{ent.real}</mark>;
  if (mode === "real" && ent.action === "faker") return <mark className="tok tok-real" title="Restored">{ent.real}</mark>;
  return <mark className={`tok ${ent.action === "redact" ? "tok-red" : "tok-fake"}`} title={ent.label}>{ent.shown}</mark>;
}

export function PromptDemo() {
  const [text, setText] = useState(SAMPLES[0].text);
  const [sample, setSample] = useState(0);
  const [stage, setStage] = useState(0);
  const [auto, setAuto] = useState(true);

  const ents = useMemo(() => detect(text), [text]);
  const segs = useMemo(() => segments(text, ents), [text, ents]);
  const reply = useMemo(() => buildReply(ents), [ents]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    ents.forEach((e) => (c[e.label] = (c[e.label] ?? 0) + 1));
    return Object.entries(c);
  }, [ents]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => setStage((s) => (s + 1) % STAGES.length), 3400);
    return () => clearInterval(t);
  }, [auto]);

  return (
    <div className="relative mx-auto max-w-[1080px]">
      <div className="absolute -inset-px rounded-[22px] bg-gradient-to-b from-[rgba(47,213,143,.45)] via-[rgba(47,213,143,.08)] to-transparent opacity-70 blur-[1px]" />
      <div className="panel relative overflow-hidden rounded-[22px] shadow-[0_40px_120px_-30px_rgba(47,213,143,.25)]">
        <div className="flex items-center gap-2 border-b border-[var(--border)] bg-black/30 px-4 py-3">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
          <div className="mx-auto flex items-center gap-2 rounded-md bg-white/[0.04] px-4 py-1 text-[12px] text-[var(--muted)]">
            <span className="pulse-dot !h-1.5 !w-1.5" /> Doppel is protecting this tab
          </div>
          <span className="w-12" />
        </div>

        <div className="grid lg:grid-cols-2">
          <div className="border-b border-[var(--border)] p-5 lg:border-b-0 lg:border-r lg:p-7">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <span className="text-[12px] font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">Your prompt (editable)</span>
              <div className="flex flex-wrap gap-1.5">
                {SAMPLES.map((s, i) => (
                  <button
                    key={s.name}
                    onClick={() => { setSample(i); setText(s.text); }}
                    className={`rounded-full border px-3 py-1 text-[12px] transition-colors ${sample === i ? "border-[var(--primary)] bg-[rgba(47,213,143,.1)] text-[var(--primary)]" : "border-[var(--border-strong)] text-[var(--body)] hover:text-[var(--ink)]"}`}
                  >
                    {s.name}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              value={text}
              onChange={(e) => { setText(e.target.value); setSample(-1); }}
              rows={6}
              spellCheck={false}
              className="w-full resize-none rounded-xl border border-[var(--border)] bg-black/30 p-4 text-[14.5px] leading-[1.7] text-[var(--ink)] outline-none transition-colors focus:border-[var(--primary)]"
            />
            <div className="mt-4 flex min-h-[28px] flex-wrap items-center gap-2">
              <span className="text-[12px] text-[var(--muted)]">Detected:</span>
              {counts.length === 0 && <span className="text-[12px] text-[var(--muted)]">nothing yet</span>}
              {counts.map(([l, n]) => (
                <span key={l} className="mono-code rounded-md border border-[var(--border-strong)] bg-white/[0.03] px-2 py-0.5 text-[11px] text-[var(--body)]">{l} ×{n}</span>
              ))}
            </div>
            <p className="mt-4 text-[12px] leading-[1.6] text-[var(--muted)]">
              Demo only: simplified in-browser rules. The real engine runs on your device with checksum validation and a semantic model.
            </p>
          </div>

          <div className="p-5 lg:p-7">
            <div className="mb-4 flex items-center gap-1 rounded-full border border-[var(--border)] bg-black/30 p-1">
              {STAGES.map((s, i) => {
                const Icon = s.icon;
                return (
                  <button
                    key={s.id}
                    onClick={() => { setStage(i); setAuto(false); }}
                    className={`relative flex flex-1 items-center justify-center gap-2 rounded-full px-3 py-2 text-[13px] font-medium transition-colors ${stage === i ? "text-[var(--bg)]" : "text-[var(--body)] hover:text-[var(--ink)]"}`}
                  >
                    {stage === i && <motion.span layoutId="stage-pill" className="absolute inset-0 rounded-full bg-[var(--primary)]" transition={{ type: "spring", stiffness: 380, damping: 32 }} />}
                    <Icon className="relative h-3.5 w-3.5" />
                    <span className="relative">{s.label}</span>
                  </button>
                );
              })}
            </div>

            <div className="min-h-[190px] rounded-xl border border-[var(--border)] bg-black/30 p-4 text-[14.5px] leading-[1.9] text-[var(--ink)]">
              <AnimatePresence mode="wait">
                <motion.div key={stage} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.25 }}>
                  {stage === 0 && segs.map((s, i) => (s.ent ? <Tok key={i} ent={s.ent} mode="ai" /> : <span key={i}>{s.text.split("").length ? s.text : ""}</span>)).map((n, i) => ({ ...n, key: i }))}
                  {stage === 0 && segs.length === 0 && <span className="text-[var(--muted)]">Type something on the left.</span>}
                  {stage === 1 && reply.map((p, i) => (p.ent ? <Tok key={i} ent={p.ent} mode="ai" /> : <span key={i}>{p.t}</span>))}
                  {stage === 2 && reply.map((p, i) => (p.ent ? <Tok key={i} ent={p.ent} mode="real" /> : <span key={i}>{p.t}</span>))}
                </motion.div>
              </AnimatePresence>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12px] text-[var(--muted)]">
              <span>{STAGES[stage].note}</span>
              <span className="ml-auto flex items-center gap-3">
                <span className="flex items-center gap-1.5"><i className="tok tok-fake !h-2.5 !w-2.5 !p-0" /> stand-in</span>
                <span className="flex items-center gap-1.5"><i className="tok tok-red !h-2.5 !w-2.5 !p-0" /> redacted</span>
                <span className="flex items-center gap-1.5"><i className="tok tok-real !h-2.5 !w-2.5 !p-0" /> restored</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
