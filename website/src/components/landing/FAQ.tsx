"use client";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Reveal } from "./Reveal";
import { SectionHeader } from "./bits";

const FAQS = [
  { q: "Does any personal data leave the device?", a: "No. Only stand-ins and redaction placeholders reach the AI provider. Detection, masking and the value vault all run locally." },
  { q: "What happens if the gateway is unavailable?", a: "The request is held. If the local service is down, a critical detector fails or the result cannot be verified, nothing is sent." },
  { q: "Which AI tools are supported?", a: "ChatGPT and Gemini today, for both prompts and file uploads. Other tools are future work." },
  { q: "Can it handle file uploads?", a: "Yes: text, CSV, PDF, Word and Excel are masked and keep their format. Scanned PDFs and images are blocked because they cannot be inspected without OCR." },
  { q: "Will the AI's answers still make sense?", a: "Yes. Names, emails and phone numbers get realistic stand-ins, so the model reasons normally. Replies are restored on your screen with the real values." },
  { q: "What does the admin see?", a: "Counts and categories of data masked, latency and policy changes. Never the prompt, the reply or any value." },
  { q: "How long does deployment take?", a: "Each device runs the local backend and the browser extension. The cloud control plane for policies and audit is optional and self-hosted." },
];

export function FAQ() {
  return (
    <section id="faq" className="relative border-y border-[var(--border)] bg-[var(--surface)]/40 py-24 lg:py-32">
      <div className="container-x">
        <div className="grid gap-12 lg:grid-cols-[420px_1fr] lg:gap-16">
          <div className="lg:sticky lg:top-28 lg:self-start">
            <SectionHeader eyebrow="FAQ" title="Common questions." desc="What security teams ask before rolling this out." />
          </div>
          <Reveal className="panel p-2">
            <Accordion type="single" collapsible defaultValue="item-0">
              {FAQS.map((f, i) => (
                <AccordionItem key={f.q} value={`item-${i}`} className="border-b border-[var(--border)] last:border-b-0">
                  <AccordionTrigger className="rounded-lg px-5 py-4 text-left text-[16px] font-medium text-[var(--ink)] hover:bg-white/[0.03] hover:no-underline">{f.q}</AccordionTrigger>
                  <AccordionContent className="px-5 pb-5 pt-1 text-[15px] leading-[1.65] text-[var(--body)]">{f.a}</AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
