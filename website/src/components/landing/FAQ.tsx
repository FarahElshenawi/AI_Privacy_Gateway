"use client";

import { motion } from "framer-motion";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Reveal } from "./Reveal";

const FAQS = [
  { q: "Does any personal data leave the device?", a: "No. Only masked substitutes reach the AI provider." },
  { q: "What happens if the gateway is unavailable?", a: "The request is held. Nothing passes through unchecked." },
  { q: "Which AI tools are supported?", a: "ChatGPT and Gemini are available today. Others are in private preview." },
  { q: "Can it handle file uploads?", a: "Yes. PDF, Word, Excel, and plain text are preserved with sensitive values swapped." },
  { q: "What does the admin see?", a: "Counts and categories of data masked. Never the prompt itself." },
  { q: "How long does deployment take?", a: "Most teams are live within a day." },
];

export function FAQ() {
  return (
    <section id="resources" className="relative py-24 lg:py-32 border-y border-[var(--ink)]/[0.10] bg-[var(--surface)]/60">
      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <div className="grid lg:grid-cols-[420px_1fr] gap-12 lg:gap-16">
          <div className="lg:sticky lg:top-28 lg:self-start">
            <Reveal>
              <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
                Common questions.
              </h2>
              <p className="mt-4 text-[17px] leading-[1.55] text-[var(--mist-dim)] font-medium">
                What security teams ask before rolling this out.
              </p>
            </Reveal>
          </div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-80px" }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="refined-card rounded-2xl p-2"
          >
            <Accordion type="single" collapsible defaultValue="item-0">
              {FAQS.map((f, i) => (
                <AccordionItem
                  key={i}
                  value={`item-${i}`}
                  className="border-b border-[var(--ink)]/[0.10] last:border-b-0"
                >
                  <AccordionTrigger className="px-5 py-4 text-left text-[16px] font-medium text-[var(--mist)] hover:no-underline hover:bg-[var(--ink)]/[0.03] rounded-lg">
                    {f.q}
                  </AccordionTrigger>
                  <AccordionContent className="px-5 pb-5 pt-1 text-[15px] leading-[1.6] text-[var(--mist-dim)]">
                    {f.a}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
