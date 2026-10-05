"use client";

import { motion } from "framer-motion";
import { ArrowRight } from "lucide-react";

export function CTA() {
  return (
    <section className="relative py-24 lg:py-32 overflow-hidden">
      <div className="absolute inset-0 -z-10 bg-grid-faint opacity-50" />
      <div
        className="absolute inset-0 -z-10"
        style={{
          background:
            "radial-gradient(50% 50% at 50% 50%, rgba(47,213,143,0.15) 0%, transparent 70%)",
        }}
      />

      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 24, rotate: -1 }}
          whileInView={{ opacity: 1, y: 0, rotate: -1 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="refined-card-lg rounded-[28px] p-10 sm:p-14 lg:p-20 text-center"
          style={{ transform: "rotate(-1deg)" }}
        >
          <h2 className="text-[36px] sm:text-[48px] lg:text-[64px] font-semibold serif tracking-[-0.025em] leading-[1.02] text-[var(--ink)] max-w-[760px] mx-auto">
            Let your teams use AI.
            <br />
            Keep your data.
          </h2>

          <p className="mt-6 text-[18px] leading-[1.5] text-[var(--mist-dim)] max-w-[480px] mx-auto font-medium">
            Deploy in a day. See the gateway protect your first prompt in minutes.
          </p>

          <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-3">
            <a
              href="#demo"
              className="btn-lime px-7 py-4 text-[15px] inline-flex items-center gap-2.5 w-full sm:w-auto justify-center"
              style={{ transform: "rotate(-1.5deg)" }}
            >
              <span>Book a demo</span>
              <ArrowRight className="h-4 w-4" />
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
