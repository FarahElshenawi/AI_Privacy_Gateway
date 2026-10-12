"use client";

import { Cloud, Globe, Server, X, Check } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const TIERS = [
  { icon: Globe, label: "Browser", title: "Lightweight forwarder", body: "Pauses the request and hands it to the local process. Holds no detection logic and no sensitive data.", note: "Untrusted" },
  { icon: Server, label: "Your device", title: "All detection happens here", body: "Detection, masking, verification and the encrypted value vault stay on the same machine.", note: "Trusted" },
  { icon: Cloud, label: "Optional cloud", title: "Policies and counts only", body: "Pushes policy to devices and receives metadata. Off by default, self-hosted by the customer.", note: "Off by default" },
];

function Link({ vertical = false }: { vertical?: boolean }) {
  return <div className={`flow-line ${vertical ? "v mx-auto" : ""}`}><span className="flow-dot" /></div>;
}

export function Architecture() {
  return (
    <section id="architecture" className="relative border-y border-[var(--border)] bg-[var(--surface)]/40 py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="Architecture" title="Two layers on your machine. The cloud never sees the message." desc="The browser hands off to a local process. The optional cloud control plane is hosted by the customer and carries metadata only." />

        <div className="mt-14 flex flex-col items-stretch gap-3 lg:grid lg:grid-cols-[1fr_90px_1fr_90px_1fr] lg:items-center lg:gap-0">
          {TIERS.map((t, i) => {
            const Icon = t.icon;
            return (
              <div key={t.label} className="contents">
                <Reveal delay={i * 0.1}>
                  <SpotlightCard className={`h-full p-6 ${i === 1 ? "!border-[rgba(47,213,143,.45)]" : ""}`}>
                    <div className="flex items-center justify-between">
                      <span className={`grid h-11 w-11 place-items-center rounded-xl ${i === 1 ? "bg-[var(--primary)] text-[var(--bg)]" : "border border-[var(--border-strong)] text-[var(--ink)]"}`}><Icon className="h-5 w-5" strokeWidth={2.25} /></span>
                      <span className="rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 text-[11px] uppercase tracking-[0.14em] text-[var(--body)]">{t.note}</span>
                    </div>
                    <div className="mt-5 text-[12px] uppercase tracking-[0.18em] text-[var(--muted)]">{t.label}</div>
                    <h3 className="serif mt-1.5 text-[22px] text-[var(--ink)]">{t.title}</h3>
                    <p className="mt-3 text-[15px] leading-[1.65] text-[var(--body)]">{t.body}</p>
                  </SpotlightCard>
                </Reveal>
                {i < 2 && (<><div className="hidden px-2 lg:block"><Link /></div><div className="h-10 lg:hidden"><Link vertical /></div></>)}
              </div>
            );
          })}
        </div>

        <div className="mt-8 grid gap-4 lg:grid-cols-2">
          <Reveal>
            <div className="panel h-full p-6">
              <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-[var(--primary)]"><Check className="h-4 w-4" /> What the cloud receives</div>
              <pre className="mono-code overflow-x-auto rounded-xl border border-[var(--border)] bg-black/30 p-4 text-[12.5px] leading-[1.7] text-[var(--body)]">{`{
  "event_type": "mask",
  "entity_types": { "PERSON": 2, "EMAIL": 1 },
  "entity_count": 3,
  "latency_ms": 41
}`}</pre>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <div className="panel h-full p-6">
              <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-[var(--danger)]"><X className="h-4 w-4" /> What it never receives</div>
              <ul className="space-y-2.5 text-[15px] text-[var(--body)]">
                {["Prompt text, masked or unmasked", "The AI's replies", "Real or stand-in values", "File contents", "The value vault"].map((x) => (
                  <li key={x} className="flex items-center gap-3"><X className="h-4 w-4 shrink-0 text-[var(--danger)]" /> {x}</li>
                ))}
              </ul>
              <p className="mt-4 text-[13px] text-[var(--muted)]">The audit schema rejects any extra field, so this is enforced structurally.</p>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
