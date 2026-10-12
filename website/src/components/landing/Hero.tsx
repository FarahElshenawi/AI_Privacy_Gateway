"use client";

import { motion } from "framer-motion";
import { ArrowRight, Check } from "lucide-react";
import { CTA_HREF, CTA_LABEL } from "@/lib/site";
import { PromptDemo } from "./PromptDemo";

const WORDS = [
  { w: "Use" }, { w: "AI" }, { w: "without" },
  { w: "exposing", g: true }, { w: "your", g: true }, { w: "data.", g: true },
];

export function Hero() {
  return (
    <section id="top" className="relative overflow-hidden pb-24 pt-32 lg:pb-32 lg:pt-40">
      <div className="aurora"><i /><i /><i /></div>
      <div className="bg-grid absolute inset-0 -z-0" />

      <div className="container-x relative">
        <motion.a
          href="#how-it-works"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mx-auto mb-8 flex w-fit items-center gap-2.5 rounded-full border border-[var(--border-strong)] bg-white/[0.03] py-1.5 pl-3 pr-4 text-[13px] text-[var(--body)] transition-colors hover:border-[var(--primary)]"
        >
          <span className="pulse-dot" />
          <span>Protecting ChatGPT and Gemini today</span>
          <ArrowRight className="h-3.5 w-3.5 text-[var(--primary)]" />
        </motion.a>

        <h1 className="serif mx-auto max-w-[940px] text-center text-[44px] leading-[1.04] sm:text-[64px] lg:text-[84px]">
          {WORDS.map((x, i) => (
            <motion.span
              key={i}
              initial={{ opacity: 0, y: 24, filter: "blur(8px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              transition={{ duration: 0.7, delay: 0.08 + i * 0.07, ease: [0.16, 1, 0.3, 1] }}
              className={`mr-[0.25em] inline-block ${x.g ? "text-gradient" : "text-[var(--ink)]"}`}
            >
              {x.w}
            </motion.span>
          ))}
        </h1>

        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.6 }}
          className="mx-auto mt-7 max-w-[660px] text-center text-[18px] leading-[1.65] text-[var(--body)]"
        >
          Doppel swaps names, numbers, secrets and files for realistic stand-ins on the employee&apos;s own device, before the prompt leaves the browser, then shows the real values again in the answer.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.72 }}
          className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row"
        >
          <a href={CTA_HREF} className="btn-primary w-full sm:w-auto">
            {CTA_LABEL}
            <ArrowRight className="h-4 w-4" />
          </a>
          <a href="#demo" className="btn-ghost w-full sm:w-auto">Try the live demo</a>
        </motion.div>

        <motion.ul
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.8, delay: 0.95 }}
          className="mt-8 flex flex-wrap items-center justify-center gap-x-7 gap-y-2 text-[13px] text-[var(--muted)]"
        >
          {["Masks on the device", "Fails closed, never open", "Admins see counts, never content"].map((t) => (
            <li key={t} className="flex items-center gap-2">
              <Check className="h-3.5 w-3.5 text-[var(--primary)]" strokeWidth={3} />
              {t}
            </li>
          ))}
        </motion.ul>

        <motion.div
          id="demo"
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, delay: 1.0, ease: [0.16, 1, 0.3, 1] }}
          className="mt-16 lg:mt-20"
        >
          <PromptDemo />
        </motion.div>
      </div>
    </section>
  );
}
