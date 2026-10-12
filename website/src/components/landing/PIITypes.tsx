"use client";

import { motion, AnimatePresence } from "framer-motion";
import { useState } from "react";
import { Reveal } from "./Reveal";
import { SectionHeader } from "./bits";

const CATEGORIES = [
  {
    label: "Identity",
    color: "rgba(47,213,143,1)",
    items: ["Person names", "Emails", "Phone numbers", "Usernames", "Dates of birth", "Addresses"],
  },
  {
    label: "Financial",
    color: "rgba(94,234,212,1)",
    items: ["Credit cards (Luhn)", "CVV & expiry", "IBAN (mod-97)", "SWIFT/BIC", "ABA routing", "Bank accounts", "Crypto wallets"],
  },
  {
    label: "Government & Health",
    color: "rgba(229,181,103,1)",
    items: ["US SSN", "Tax IDs", "Medical record numbers", "Health insurance IDs", "Passport numbers", "Driver's license"],
  },
  {
    label: "Secrets & Infrastructure",
    color: "rgba(224,96,91,1)",
    items: ["API keys (AWS, GitHub, Stripe)", "JWT & bearer tokens", "Private keys (PEM)", "Passwords", "Connection strings", "Cloud secrets", "Recovery codes", "Private IPs", "Internal hostnames", "Custom deny-terms"],
  },
];

export function PIITypes() {
  const [active, setActive] = useState(0);

  return (
    <section id="coverage" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader
          eyebrow="Coverage"
          title={<>24+ entity types, <span className="text-gradient">two detection tiers</span>.</>}
          desc="Tier 1 catches structured data with hard checksums in under a millisecond. Tier 2 uses a semantic model for names, addresses, and organizations that regex can't recognize."
        />

        <div className="mt-14 grid lg:grid-cols-[280px_1fr] gap-6">
          {/* Category tabs */}
          <div className="flex flex-col gap-2">
            {CATEGORIES.map((cat, i) => {
              const on = active === i;
              return (
                <button
                  key={cat.label}
                  onClick={() => setActive(i)}
                  className={`relative overflow-hidden rounded-xl border p-4 text-left transition-all ${
                    on ? "border-[var(--border-strong)] bg-[var(--surface)]" : "border-[var(--border)] hover:border-[var(--border-strong)]"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ background: cat.color, boxShadow: on ? `0 0 12px ${cat.color}` : "none" }}
                    />
                    <span className={`text-[15px] font-medium ${on ? "text-[var(--ink)]" : "text-[var(--body)]"}`}>
                      {cat.label}
                    </span>
                    <span className="ml-auto mono-code text-[12px] text-[var(--muted)]">
                      {cat.items.length}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Items grid */}
          <div className="panel p-6 lg:p-8">
            <AnimatePresence mode="wait">
              <motion.div
                key={active}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -12 }}
                transition={{ duration: 0.25 }}
              >
                <div className="flex flex-wrap gap-2.5">
                  {CATEGORIES[active].items.map((item) => (
                    <span
                      key={item}
                      className="rounded-lg border border-[var(--border)] bg-[var(--subtle)] px-3.5 py-2 text-[14px] text-[var(--ink)]"
                      style={{ borderLeftColor: CATEGORIES[active].color, borderLeftWidth: 2 }}
                    >
                      {item}
                    </span>
                  ))}
                </div>
                <p className="mt-6 text-[13px] text-[var(--muted)]">
                  Each item is tagged with its category for audit logging. Every masked entity is counted — never its value.
                </p>
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </section>
  );
}
