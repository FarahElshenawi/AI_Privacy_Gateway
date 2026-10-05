"use client";

import { motion } from "framer-motion";
import { ShieldCheck, ArrowRight } from "lucide-react";

// Before/after card — the hero's one creative element.
// Shows what the employee types on the left, what the AI receives on the right,
// with the Doppel shield logo in the middle and a thin green arrow between them.

const BEFORE = {
  pre: "Send the contract to ",
  highlight: "Sarah Mitchell",
  mid: ", card ",
  highlight2: "4532 8891 2204 7712",
  post: ".",
};

const AFTER = {
  pre: "Send the contract to ",
  highlight: "Emma Collins",
  mid: ", card ",
  highlight2: "4916 3307 5518 0294",
  post: ".",
};

export function BeforeAfterCard() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.7, delay: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="relative w-full max-w-[1120px] mx-auto"
    >
      <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] items-stretch gap-6 md:gap-4">
        {/* LEFT — what the employee types */}
        <motion.div
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, delay: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="refined-card p-6 lg:p-7"
        >
          <div className="eyebrow mb-4">What the employee types</div>
          <div className="text-[18px] lg:text-[20px] leading-[1.55] text-[var(--ink)]">
            {BEFORE.pre}
            <Highlighted variant="before">{BEFORE.highlight}</Highlighted>
            {BEFORE.mid}
            <Highlighted variant="before">{BEFORE.highlight2}</Highlighted>
            {BEFORE.post}
          </div>
        </motion.div>

        {/* CENTER — shield logo + arrow connector (horizontal on desktop, vertical on mobile) */}
        <div className="flex md:flex-col items-center justify-center gap-3 md:gap-4 py-2 md:py-0">
          {/* The shield in the middle — same logo treatment as Navbar */}
          <div
            className="grid place-items-center h-14 w-14 rounded-2xl flex-shrink-0"
            style={{
              background: "var(--primary)",
              border: "1px solid var(--primary)",
              boxShadow: "none",
            }}
          >
            <ShieldCheck
              className="h-7 w-7"
              style={{ color: "var(--bg)" }}
              strokeWidth={2.5}
            />
          </div>
          {/* Thin green arrow — horizontal on desktop, vertical on mobile */}
          <ArrowRight
            className="h-5 w-5 hidden md:block"
            style={{ color: "var(--primary)" }}
            strokeWidth={2}
          />
          <ArrowRight
            className="h-5 w-5 md:hidden rotate-90"
            style={{ color: "var(--primary)" }}
            strokeWidth={2}
          />
        </div>

        {/* RIGHT — what the AI receives */}
        <motion.div
          initial={{ opacity: 0, x: 16 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, delay: 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="refined-card p-6 lg:p-7"
          style={{
            borderColor: "var(--primary)",
            borderWidth: "1.5px",
          }}
        >
          <div className="eyebrow mb-4">What the AI receives</div>
          <div className="text-[18px] lg:text-[20px] leading-[1.55] text-[var(--ink)]">
            {AFTER.pre}
            <Highlighted variant="after">{AFTER.highlight}</Highlighted>
            {AFTER.mid}
            <Highlighted variant="after">{AFTER.highlight2}</Highlighted>
            {AFTER.post}
          </div>
        </motion.div>
      </div>
    </motion.div>
  );
}

function Highlighted({
  children,
  variant,
}: {
  children: React.ReactNode;
  variant: "before" | "after";
}) {
  return (
    <span
      className="inline-block font-medium rounded-md px-1.5 py-0.5 mx-0.5"
      style={{
        background:
          variant === "before"
            ? "rgba(47, 213, 143, 0.10)"
            : "rgba(47, 213, 143, 0.18)",
        color: "var(--primary)",
        border: `1px solid ${
          variant === "before"
            ? "rgba(47, 213, 143, 0.20)"
            : "rgba(47, 213, 143, 0.45)"
        }`,
      }}
    >
      {children}
    </span>
  );
}
