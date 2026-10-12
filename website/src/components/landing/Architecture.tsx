"use client";

import { motion } from "framer-motion";
import { Globe, Server, Cloud, ArrowRight } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const TIERS = [
  {
    icon: Globe,
    label: "Browser",
    title: "Lightweight forwarder",
    body: "Intercepts the request at the network layer, below the page's own scripts. Holds no sensitive data — just forwards to the local backend.",
    note: "Untrusted",
    noteColor: "var(--danger)",
  },
  {
    icon: Server,
    label: "Your device",
    title: "All detection here",
    body: "Two-tier detection, offset masking, the encrypted vault, and the residual scanner. Everything that matters stays on this machine.",
    note: "Trusted",
    noteColor: "var(--primary)",
  },
  {
    icon: Cloud,
    label: "Optional cloud",
    title: "Counts only",
    body: "If enabled, the cloud control plane sees entity counts and timing. Never the prompt, never the masked text, never the real value.",
    note: "Off by default",
    noteColor: "var(--muted)",
  },
];

export function Architecture() {
  return (
    <section id="platform" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader
          eyebrow="Architecture"
          title={<>Two tiers on your machine. The cloud <span className="text-gradient">never sees the message</span>.</>}
          desc="The browser hands off to a local process. Detection, masking, and the vault run on the same machine — never in the cloud."
        />

        <div className="mt-14 grid lg:grid-cols-3 gap-5">
          {TIERS.map((t, i) => {
            const Icon = t.icon;
            return (
              <Reveal key={t.label} delay={i * 0.1}>
                <SpotlightCard className="h-full p-7">
                  <div className="flex items-center justify-between">
                    <span className="grid h-11 w-11 place-items-center rounded-xl border border-[var(--border-strong)] bg-[var(--subtle)]">
                      <Icon className="h-5 w-5" strokeWidth={2.25} style={{ color: t.noteColor === "var(--danger)" ? "var(--danger)" : t.noteColor === "var(--primary)" ? "var(--primary)" : "var(--body)" }} />
                    </span>
                    <span
                      className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider"
                      style={{
                        background: `color-mix(in srgb, ${t.noteColor} 12%, transparent)`,
                        color: t.noteColor,
                      }}
                    >
                      {t.note}
                    </span>
                  </div>
                  <div className="mono-code mt-5 text-[12px] uppercase tracking-[0.14em] text-[var(--muted)]">
                    {t.label}
                  </div>
                  <h3 className="serif mt-2 text-[22px] text-[var(--ink)]">{t.title}</h3>
                  <p className="mt-3 text-[15px] leading-[1.6] text-[var(--body)]">{t.body}</p>
                </SpotlightCard>
              </Reveal>
            );
          })}
        </div>

        {/* Data flow visual */}
        <Reveal delay={0.3}>
          <div className="mt-8 panel p-6">
            <div className="flex flex-col lg:flex-row items-center gap-4 text-[13px]">
              <div className="flex items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--subtle)] px-4 py-2.5">
                <span className="h-2 w-2 rounded-full bg-[var(--danger)]" />
                <span className="text-[var(--body)]">Prompt + files</span>
              </div>
              <div className="flex-1 max-w-[120px] w-full">
                <div className="flow-line">
                  <div className="flow-dot" />
                </div>
              </div>
              <div className="flex items-center gap-2.5 rounded-lg border border-[var(--primary)]/40 bg-[rgba(47,213,143,.06)] px-4 py-2.5">
                <span className="h-2 w-2 rounded-full bg-[var(--primary)]" />
                <span className="text-[var(--ink)]">Masked surrogates →</span>
              </div>
              <div className="flex-1 max-w-[120px] w-full">
                <div className="flow-line">
                  <div className="flow-dot" />
                </div>
              </div>
              <div className="flex items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--subtle)] px-4 py-2.5">
                <span className="h-2 w-2 rounded-full bg-[var(--sky)]" />
                <span className="text-[var(--body)]">AI sees fakes only</span>
              </div>
            </div>
            <p className="mt-4 text-center text-[12px] text-[var(--muted)]">
              The AI provider receives only surrogates. Real values are restored on the employee's screen after the reply.
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
