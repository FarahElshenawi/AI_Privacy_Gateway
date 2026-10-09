"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import { BeforeAfterCard } from "./BeforeAfterCard";
import { CTA_HREF, CTA_LABEL } from "@/lib/site";

export function Hero() {
  return (
    <section
      id="top"
      className="relative pt-32 pb-24 lg:pt-40 lg:pb-32 overflow-hidden"
    >
      {/* Faint grid only — no glow, no drift */}
      <div className="absolute inset-0 -z-10 bg-grid-strong" />

      <div className="mx-auto max-w-[1120px] px-5 lg:px-8">
        {/* Eyebrow */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="text-center mb-7"
        >
          <span className="eyebrow">AI Privacy Gateway</span>
        </motion.div>

        {/* Headline — single sentence, no rotating phrase, no italic */}
        <motion.h1
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.05, ease: [0.16, 1, 0.3, 1] }}
          className="text-center font-semibold serif tracking-[-0.02em] text-[var(--ink)] text-[44px] sm:text-[60px] lg:text-[80px] leading-[1.05]"
        >
          Use AI without exposing your data.
        </motion.h1>

        {/* Subline — one sentence */}
        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
          className="mx-auto mt-6 max-w-[640px] text-center text-[18px] leading-[1.6] text-[var(--body)]"
        >
          Doppel replaces names, numbers and files with stand-ins on the
          employee&apos;s device before the prompt is sent, and shows the real
          values again in the answer on screen.
        </motion.p>

        {/* Buttons — one emerald primary + one text link */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="mt-9 flex flex-col sm:flex-row items-center justify-center gap-5"
        >
          <a
            href={CTA_HREF}
            className="btn-lime px-6 py-3.5 text-[15px] inline-flex items-center gap-2 w-full sm:w-auto justify-center"
          >
            <span>{CTA_LABEL}</span>
            <ArrowRight className="h-4 w-4" />
          </a>
          <a href="#how-it-works" className="text-link">
            <span>See how it works</span>
            <ArrowRight className="h-4 w-4" />
          </a>
        </motion.div>

        {/* Before / After card — the centerpiece */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.45, ease: [0.16, 1, 0.3, 1] }}
          className="mt-20"
        >
          <BeforeAfterCard />
        </motion.div>
      </div>
    </section>
  );
}
