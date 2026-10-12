"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader } from "./bits";

const FAQS = [
  {
    q: "Does any personal data leave the device?",
    a: "Only masked surrogates reach the AI provider. Real values are stored in an encrypted vault on the employee's machine — never uploaded to the cloud or the AI provider's servers.",
  },
  {
    q: "What happens if the gateway is unavailable?",
    a: "The request is held. Nothing passes through unchecked. The extension blocks the upload or message send until the local backend is back online and masking succeeds.",
  },
  {
    q: "Which AI tools are supported?",
    a: "ChatGPT and Gemini are supported today — both text prompts and file uploads. The architecture is extensible to other providers.",
  },
  {
    q: "Can it handle file uploads?",
    a: "Yes. PDF, Word (.docx), Excel (.xlsx), and plain text (TXT/CSV/MD) are masked in place. File type is detected from magic bytes, not the extension. Scanned PDFs and images are blocked.",
  },
  {
    q: "What does the admin dashboard see?",
    a: "Entity counts and categories (e.g., 3 PERSONs, 2 EMAILs masked), timing, and fail-closed events. Never the prompt text, never the masked output, never the real values. The audit log is metadata-only by design.",
  },
  {
    q: "How does detection work?",
    a: "Two tiers run in parallel. Tier 1 uses regex patterns and hard validators (Luhn checksums, mod-97, base58check) — it catches credit cards, SSNs, API keys, IBANs, and other structured data in under a millisecond. Tier 2 uses a semantic AI model (GLiNER2-PII, 205M params) for names, addresses, and organizations that regex can't recognize.",
  },
  {
    q: "Is the cloud control plane required?",
    a: "No. The local backend runs standalone. The optional cloud provides policy management (set PERSON to redact, CREDIT_CARD to faker), audit collection, and fleet tracking. If the cloud is down, the local backend continues masking with its last-known policy.",
  },
  {
    q: "What about reply demasking?",
    a: "The AI's reply contains fake values. A content script restores real values in the page, but only in assistant messages — never in the input box. This can be toggled off from the popup. Note: restored values exist in the page DOM, which the AI provider's own JavaScript could theoretically read.",
  },
];

function FAQItem({ q, a, isOpen, onClick }: { q: string; a: string; isOpen: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full border-b border-[var(--border)] last:border-b-0 px-1 py-5 text-left transition-colors hover:bg-[var(--subtle)]/40"
    >
      <div className="flex items-center justify-between gap-4">
        <span className="text-[16px] font-medium text-[var(--ink)]">{q}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform ${isOpen ? "rotate-180" : ""}`}
        />
      </div>
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <p className="pt-3 text-[15px] leading-[1.65] text-[var(--body)]">{a}</p>
          </motion.div>
        )}
      </AnimatePresence>
    </button>
  );
}

export function FAQ() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section id="faq" className="relative py-24 lg:py-32">
      <div className="container-x">
        <div className="grid lg:grid-cols-[400px_1fr] gap-12 lg:gap-16">
          <div className="lg:sticky lg:top-28 lg:self-start">
            <SectionHeader
              eyebrow="FAQ"
              title={<>Common <span className="text-gradient">questions</span>.</>}
              desc="What security teams ask before rolling this out."
            />
          </div>

          <Reveal delay={0.1}>
            <div className="panel p-2 px-6">
              {FAQS.map((f, i) => (
                <FAQItem
                  key={i}
                  q={f.q}
                  a={f.a}
                  isOpen={open === i}
                  onClick={() => setOpen(open === i ? null : i)}
                />
              ))}
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
