"use client";

import { motion } from "framer-motion";
import { CreditCard, KeyRound, User, Building2 } from "lucide-react";
import { Reveal } from "./Reveal";

const CARDS = [
  {
    icon: User,
    color: "#2FD58F",
    title: "Names & contact",
    items: "Person names · emails · phone numbers",
  },
  {
    icon: CreditCard,
    color: "#2FD58F",
    title: "Financial data",
    items: "Card numbers · account numbers · bank details",
  },
  {
    icon: KeyRound,
    color: "#2FD58F",
    title: "Secrets & keys",
    items: "API keys · tokens · certificates · passwords",
  },
  {
    icon: Building2,
    color: "#2FD58F",
    title: "Organizations & places",
    items: "Company names · addresses · locations",
  },
];

export function PIITypes() {
  return (
    <section id="coverage" className="relative py-24 lg:py-32 border-y border-[var(--ink)]/[0.10]">
      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <div className="max-w-[640px]">
          <Reveal>
            <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
              What we protect.
            </h2>
            <p className="mt-4 text-[17px] leading-[1.55] text-[var(--mist-dim)] font-medium">
              The categories of sensitive data the gateway recognizes and masks automatically.
            </p>
          </Reveal>
        </div>

        <div className="mt-14 grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {CARDS.map((c, i) => {
            const Icon = c.icon;
            return (
              <motion.article
                key={c.title}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="refined-card rounded-2xl p-7"
                style={{ transform: `rotate(${i % 2 === 0 ? -1 : 1}deg)` }}
              >
                <span
                  className="grid place-items-center h-11 w-11 rounded-xl border border-[var(--border)]"
                  style={{ background: c.color, boxShadow: "0 6px 16px -6px rgba(47, 213, 143, 0.25)" }}
                >
                  <Icon className="h-5 w-5 text-[var(--ink)]" strokeWidth={3} />
                </span>
                <h3 className="mt-5 text-[18px] font-semibold serif text-[var(--ink)]">
                  {c.title}
                </h3>
                <p className="mt-2.5 text-[14px] leading-[1.6] text-[var(--mist-dim)]">
                  {c.items}
                </p>
              </motion.article>
            );
          })}
        </div>

        <p className="mt-8 text-[14px] text-[var(--mist-dim)] max-w-[640px]">
          Every masked item is tagged with its category, so your security and compliance teams can audit exactly what was caught.
        </p>
      </div>
    </section>
  );
}
