"use client";

import { motion } from "framer-motion";
import { ArrowRight, ShieldOff, KeyRound, Repeat, ShieldCheck } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const POINTS = [
  {
    icon: ShieldOff,
    title: "Nothing raw leaves",
    body: "Only masked surrogates reach the AI provider. Real values stay on the device in an encrypted vault.",
  },
  {
    icon: KeyRound,
    title: "Per-conversation vault",
    body: "Each chat session uses its own private mapping. No cross-chat fingerprinting, no leakage between conversations.",
  },
  {
    icon: Repeat,
    title: "Checked twice",
    body: "An independent residual scanner re-runs on the masked output with hard checksums (Luhn, mod-97, JWT). If anything survives, the request is blocked.",
  },
  {
    icon: ShieldCheck,
    title: "Holds by default",
    body: "If the gateway is down, the model is loading, or anything looks unsafe, the request is held — not passed through.",
  },
];

export function Difference() {
  return (
    <section id="security" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader
          eyebrow="Security model"
          title={<>Built like a <span className="text-gradient">security product</span>.</>}
          desc="Fail-closed defaults, encrypted storage, and a second-pass scanner. Designed for teams who need AI access without shipping data."
        />

        <div className="mt-14 grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {POINTS.map((p, i) => {
            const Icon = p.icon;
            return (
              <Reveal key={p.title} delay={i * 0.07}>
                <SpotlightCard className="h-full p-6">
                  <span className="grid h-11 w-11 place-items-center rounded-xl border border-[var(--border-strong)] bg-[var(--subtle)] text-[var(--primary)]">
                    <Icon className="h-5 w-5" strokeWidth={2.25} />
                  </span>
                  <h3 className="serif mt-5 text-[20px] text-[var(--ink)]">{p.title}</h3>
                  <p className="mt-2 text-[14px] leading-[1.6] text-[var(--body)]">{p.body}</p>
                </SpotlightCard>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
