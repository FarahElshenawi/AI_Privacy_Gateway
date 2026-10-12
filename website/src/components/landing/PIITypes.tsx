"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Building2, CreditCard, KeyRound, User } from "lucide-react";
import { SectionHeader, SpotlightCard } from "./bits";

const TABS = [
  {
    id: "identity", label: "Identity", icon: User, action: "Stand-in", cls: "tok-fake",
    blurb: "Names and contact details become realistic stand-ins, so the prompt still reads naturally and the reply can be restored.",
    items: ["Person names", "Emails", "Phone numbers", "Addresses", "Organizations", "Locations", "Usernames", "Dates of birth"],
    ex: [["Sarah Mitchell", "Emma Collins"], ["sarah@acme-corp.com", "m.reyes@northwind.example"]],
  },
  {
    id: "financial", label: "Financial", icon: CreditCard, action: "Redacted", cls: "tok-red",
    blurb: "Payment and banking data is validated with real checksums, then replaced with a placeholder.",
    items: ["Credit cards (Luhn)", "CVV and expiry", "IBAN (mod-97)", "SWIFT / BIC", "ABA routing", "Bank accounts", "Crypto wallets"],
    ex: [["4532 0151 1283 0366", "[REDACTED:CREDIT_CARD]"], ["DE89 3704 0044 0532 0130 00", "[[REDACTED:IBAN:3f9a]]"]],
  },
  {
    id: "gov", label: "Government and health", icon: Building2, action: "Redacted", cls: "tok-red",
    blurb: "Identifiers regulated under HIPAA and national ID rules never leave the device.",
    items: ["US SSN", "Tax IDs / EIN / VAT", "Medical record numbers", "Health insurance IDs", "Passport numbers", "Driver's licence numbers"],
    ex: [["123-45-6789", "[REDACTED:US_SSN]"], ["MRN-4821937", "[REDACTED:MEDICAL_RECORD_NUMBER]"]],
  },
  {
    id: "secrets", label: "Secrets and infrastructure", icon: KeyRound, action: "Redacted", cls: "tok-red",
    blurb: "Credentials, keys and internal hostnames are caught by prefix, entropy and structure, not guesswork.",
    items: ["API keys", "JWT and bearer tokens", "Private keys (PEM)", "Passwords", "Connection strings", "Cloud secrets", "Internal URLs and hosts", "Private IPs", "Custom deny-terms"],
    ex: [["sk-proj-4f9Kd82L...", "[REDACTED:API_KEY]"], ["postgresql://app:***@db.internal", "[REDACTED:CONNECTION_STRING]"]],
  },
];

const ACTIONS = ["Faker", "Redact", "Keep", "Block"];

export function PIITypes() {
  const [i, setI] = useState(0);
  const t = TABS[i];
  return (
    <section id="coverage" className="relative border-y border-[var(--border)] bg-[var(--surface)]/40 py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="Coverage" title="What we protect, and what happens to it." desc="Twenty-four structured types from deterministic detection, plus names, addresses and dates from a semantic model. Every action is configurable per organization." />

        <div className="mt-12 flex flex-wrap gap-2">
          {TABS.map((x, n) => {
            const Icon = x.icon;
            return (
              <button key={x.id} onClick={() => setI(n)} className={`flex items-center gap-2 rounded-full border px-4 py-2.5 text-[14px] font-medium transition-colors ${i === n ? "border-transparent bg-[var(--primary)] text-[var(--bg)]" : "border-[var(--border-strong)] text-[var(--body)] hover:text-[var(--ink)]"}`}>
                <Icon className="h-4 w-4" /> {x.label}
              </button>
            );
          })}
        </div>

        <AnimatePresence mode="wait">
          <motion.div key={t.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.28 }} className="mt-6 grid gap-4 lg:grid-cols-[1.2fr_1fr]">
            <SpotlightCard className="p-7">
              <div className="flex items-center gap-3">
                <span className={`tok ${t.cls}`}>{t.action}</span>
                <span className="text-[13px] text-[var(--muted)]">default action</span>
              </div>
              <p className="mt-4 text-[16px] leading-[1.65] text-[var(--body)]">{t.blurb}</p>
              <div className="mt-6 flex flex-wrap gap-2">
                {t.items.map((it) => (
                  <span key={it} className="rounded-lg border border-[var(--border-strong)] bg-white/[0.02] px-3 py-1.5 text-[13px] text-[var(--ink)]">{it}</span>
                ))}
              </div>
            </SpotlightCard>

            <SpotlightCard className="p-7">
              <div className="text-[12px] font-semibold uppercase tracking-[0.16em] text-[var(--muted)]">Before and after</div>
              <div className="mt-5 space-y-4">
                {t.ex.map(([a, b]) => (
                  <div key={a} className="rounded-xl border border-[var(--border)] bg-black/30 p-4 text-[13px]">
                    <span className="tok tok-orig break-all">{a}</span>
                    <div className="my-2 flex items-center gap-2 text-[var(--muted)]"><ArrowRight className="h-3.5 w-3.5" /><span className="text-[11px]">AI receives</span></div>
                    <span className={`tok ${t.cls} break-all`}>{b}</span>
                  </div>
                ))}
              </div>
            </SpotlightCard>
          </motion.div>
        </AnimatePresence>

        <div className="mt-6 flex flex-wrap items-center gap-3 text-[14px] text-[var(--body)]">
          <span>Policy actions per entity:</span>
          {ACTIONS.map((a) => <span key={a} className="mono-code rounded-md border border-[var(--border-strong)] px-2.5 py-1 text-[12px] text-[var(--ink)]">{a}</span>)}
          <span className="text-[var(--muted)]">Critical secrets can never be set to Keep.</span>
        </div>
      </div>
    </section>
  );
}
