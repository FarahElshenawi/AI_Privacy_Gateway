"use client";

import { motion } from "framer-motion";
import { Globe, Server, Cloud } from "lucide-react";
import { Reveal } from "./Reveal";

const TIERS = [
  {
    icon: Globe,
    color: "#2FD58F",
    label: "Browser",
    title: "Lightweight forwarder",
    body: "Intercepts the request. Holds no sensitive data.",
    note: "Untrusted",
  },
  {
    icon: Server,
    color: "#2FD58F",
    label: "Your device",
    title: "All detection here",
    body: "Masking, verification, and the value vault stay on the same machine.",
    note: "Trusted",
  },
  {
    icon: Cloud,
    color: "#2FD58F",
    label: "Optional cloud",
    title: "Counts only",
    body: "If enabled, the cloud sees counts only. Never the message itself.",
    note: "Off by default",
  },
];

export function Architecture() {
  return (
    <section
      id="architecture"
      className="relative py-24 lg:py-32 border-y border-[var(--ink)]/[0.10] bg-[var(--surface)]/60"
    >
      <div className="absolute inset-0 -z-10 bg-grid-faint opacity-30" />

      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <div className="max-w-[640px]">
          <Reveal>
            <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
              Two layers on your machine. The cloud never sees the message.
            </h2>
            <p className="mt-4 text-[17px] leading-[1.55] text-[var(--mist-dim)] font-medium">
              The browser hands off to a local process. The cloud is optional and sees counts only.
            </p>
          </Reveal>
        </div>

        <div className="mt-14 grid lg:grid-cols-3 gap-5">
          {TIERS.map((t, i) => {
            const Icon = t.icon;
            return (
              <motion.div
                key={t.label}
                initial={{ opacity: 0, y: 24, rotate: i === 1 ? 0 : i % 2 === 0 ? -1 : 1 }}
                whileInView={{ opacity: 1, y: 0, rotate: i === 1 ? 0 : i % 2 === 0 ? -1 : 1 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.55, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="refined-card rounded-2xl p-7"
                style={{ transform: `rotate(${i === 1 ? 0 : i % 2 === 0 ? -1 : 1}deg)` }}
              >
                <div className="flex items-center justify-between">
                  <span
                    className="grid place-items-center h-11 w-11 rounded-xl border border-[var(--border)]"
                    style={{ background: t.color, boxShadow: "0 6px 16px -6px rgba(47, 213, 143, 0.25)" }}
                  >
                    <Icon className="h-5 w-5 text-[var(--ink)]" strokeWidth={3} />
                  </span>
                  <span
                    className="refined-chip text-[11px] mono-code uppercase tracking-[0.18em] font-bold"
                    style={{ color: "var(--ink)" }}
                  >
                    {t.note}
                  </span>
                </div>
                <div className="mt-5 text-[12px] mono-code uppercase tracking-[0.18em] text-[var(--mist-dim)]">
                  {t.label}
                </div>
                <h3 className="mt-2 text-[22px] font-semibold serif text-[var(--ink)]">
                  {t.title}
                </h3>
                <p className="mt-3 text-[15px] leading-[1.6] text-[var(--mist-dim)]">
                  {t.body}
                </p>
              </motion.div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
