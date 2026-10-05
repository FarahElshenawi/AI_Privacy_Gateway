"use client";

import { motion } from "framer-motion";
import { Eye, Replace, RotateCcw } from "lucide-react";
import { Reveal } from "./Reveal";

const STEPS = [
  {
    num: "01",
    color: "var(--ink)",
    icon: Eye,
    title: "Intercept",
    desc: "Every prompt and every file is captured at the browser, before it reaches the AI tool.",
  },
  {
    num: "02",
    color: "var(--primary)",
    icon: Replace,
    title: "Mask",
    desc: "Sensitive values are swapped for realistic stand-ins on the employee's own device.",
  },
  {
    num: "03",
    color: "var(--ink)",
    icon: RotateCcw,
    title: "Restore",
    desc: "The real values are returned in the AI's reply. The user sees their actual data, the AI never did.",
  },
];

export function Pillars() {
  return (
    <section
      id="how-it-works"
      className="relative py-24 lg:py-32 overflow-hidden"
    >
      <div className="absolute inset-0 -z-10 bg-grid-faint" />

      <div className="mx-auto max-w-[1120px] px-5 lg:px-8">
        <Reveal>
          <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
            How it works.
          </h2>
          <p className="mt-4 text-[17px] leading-[1.6] text-[var(--body)] max-w-[560px]">
            Three steps, all on the employee&apos;s device. Nothing sensitive
            crosses the network.
          </p>
        </Reveal>

        <div className="mt-14 lg:mt-20 grid lg:grid-cols-3 gap-5">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            return (
              <motion.article
                key={s.num}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="refined-card p-7"
              >
                <div className="flex items-baseline justify-between">
                  <span
                    className="grid place-items-center h-11 w-11 rounded-xl border border-[var(--border)]"
                    style={{
                      color: s.color,
                      background: s.color === "var(--primary)" ? "rgba(47, 213, 143, 0.10)" : "transparent",
                    }}
                  >
                    <Icon className="h-5 w-5" strokeWidth={2.25} />
                  </span>
                  <span className="text-[36px] font-semibold serif leading-none text-[var(--muted)]/30">
                    {s.num}
                  </span>
                </div>

                <h3 className="mt-6 text-[22px] font-semibold serif tracking-tight text-[var(--ink)]">
                  {s.title}
                </h3>
                <p className="mt-3 text-[15px] leading-[1.65] text-[var(--body)]">
                  {s.desc}
                </p>
              </motion.article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
