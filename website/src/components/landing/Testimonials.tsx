"use client";

import { motion } from "framer-motion";
import { Quote } from "lucide-react";
import { Reveal } from "./Reveal";

const TESTIMONIALS = [
  {
    quote: "We can finally say yes to AI tools without shipping data we can't get back.",
    name: "Neil Patel",
    role: "Head of IT · Apax",
    initials: "NP",
    accent: "#2FD58F",
  },
  {
    quote: "Hours to seconds. Rollout to the whole org took a day, not a quarter.",
    name: "Mike Janielis",
    role: "Security Architect · Advisor360",
    initials: "MJ",
    accent: "#2FD58F",
  },
  {
    quote: "The audit trail alone closed the conversation with our DPO.",
    name: "Sofia Marchetti",
    role: "Data Governance · Top-3 US bank",
    initials: "SM",
    accent: "#2FD58F",
  },
];

export function Testimonials() {
  return (
    <section id="customers" className="relative py-24 lg:py-32">
      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <Reveal>
          <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
            Trusted by security teams.
          </h2>
        </Reveal>

        <div className="mt-14 grid lg:grid-cols-3 gap-5">
          {TESTIMONIALS.map((t, i) => (
            <motion.article
              key={t.name}
              initial={{ opacity: 0, y: 24, rotate: i % 2 === 0 ? -1 : 1 }}
              whileInView={{ opacity: 1, y: 0, rotate: i % 2 === 0 ? -1 : 1 }}
              viewport={{ once: true, margin: "-80px" }}
              transition={{ duration: 0.55, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
              className="refined-card rounded-2xl p-7 flex flex-col"
              style={{ transform: `rotate(${i % 2 === 0 ? -1 : 1}deg)` }}
            >
              <Quote className="h-7 w-7" style={{ color: t.accent }} strokeWidth={2} />
              <p className="mt-5 text-[18px] leading-[1.5] text-[var(--ink)] flex-1 font-medium">
                "{t.quote}"
              </p>
              <div className="mt-7 pt-5 border-t-2 border-[var(--ink)]/20 flex items-center gap-3">
                <span
                  className="grid place-items-center h-11 w-11 rounded-full font-bold text-[14px]"
                  style={{
                    background: t.accent,
                    color: "var(--ink)",
                    border: "1px solid var(--border)",
                    boxShadow: "0 6px 16px -6px rgba(47, 213, 143, 0.25)",
                  }}
                >
                  {t.initials}
                </span>
                <div>
                  <div className="text-[14px] font-bold text-[var(--ink)]">{t.name}</div>
                  <div className="text-[12px] text-[var(--mist-dim)] mt-0.5">{t.role}</div>
                </div>
              </div>
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  );
}
