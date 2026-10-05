"use client";

import { motion } from "framer-motion";
import { ShieldOff, KeyRound, Repeat, ShieldCheck } from "lucide-react";
import { Reveal } from "./Reveal";

const POINTS = [
  {
    icon: ShieldOff,
    color: "#2FD58F",
    title: "Nothing raw ever leaves",
    body: "Only masked values reach the AI provider. Your real data stays on the device.",
  },
  {
    icon: KeyRound,
    color: "#2FD58F",
    title: "Scoped to each conversation",
    body: "Each chat session uses its own private mapping. No cross-chat fingerprinting.",
  },
  {
    icon: Repeat,
    color: "#2FD58F",
    title: "Checked twice",
    body: "An independent second pass re-runs on the masked output. Doubts block the request.",
  },
  {
    icon: ShieldCheck,
    color: "#2FD58F",
    title: "Holds by default",
    body: "If the gateway is down or anything looks unsafe, the request is held — not passed through.",
  },
];

export function Difference() {
  return (
    <section
      id="difference"
      className="relative py-24 lg:py-32 border-y border-[var(--ink)]/[0.10] bg-[var(--surface)]/60"
    >
      <div className="absolute inset-0 -z-10 bg-grid-dot opacity-50" />

      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <div className="max-w-[640px]">
          <Reveal>
            <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
              Built like a security product.
            </h2>
            <p className="mt-4 text-[17px] leading-[1.55] text-[var(--mist-dim)] font-medium">
              Designed for security teams who need to ship AI access without shipping data.
            </p>
          </Reveal>
        </div>

        <div className="mt-14 grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {POINTS.map((p, i) => {
            const Icon = p.icon;
            return (
              <motion.article
                key={p.title}
                initial={{ opacity: 0, y: 20, rotate: i % 2 === 0 ? -1.2 : 1.2 }}
                whileInView={{ opacity: 1, y: 0, rotate: i % 2 === 0 ? -1.2 : 1.2 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="refined-card rounded-2xl p-6"
                style={{ transform: `rotate(${i % 2 === 0 ? -1.2 : 1.2}deg)` }}
              >
                <span
                  className="grid place-items-center h-10 w-10 rounded-xl border border-[var(--border)]"
                  style={{ background: p.color, boxShadow: "0 6px 16px -6px rgba(47, 213, 143, 0.25)" }}
                >
                  <Icon className="h-5 w-5 text-[var(--ink)]" strokeWidth={3} />
                </span>
                <h3 className="mt-5 text-[18px] font-semibold serif text-[var(--ink)] leading-snug">
                  {p.title}
                </h3>
                <p className="mt-2 text-[14px] leading-[1.6] text-[var(--mist-dim)]">
                  {p.body}
                </p>
              </motion.article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
