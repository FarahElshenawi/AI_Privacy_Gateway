"use client";

import { CountUp, SpotlightCard } from "./bits";
import { Reveal } from "./Reveal";

const STATS = [
  { node: <CountUp to={24} />, label: "structured entity types", sub: "Cards, banking, IDs, secrets, network" },
  { node: <CountUp to={97.4} decimals={1} suffix="%" />, label: "character-level leak recall", sub: "Tier 1, frozen hold-out of 298 cases" },
  { node: <CountUp to={0.45} decimals={2} suffix=" ms" />, label: "p95 detection latency", sub: "Tier 1 deterministic engine" },
  { node: <CountUp to={4} />, label: "file formats kept intact", sub: "TXT/CSV, PDF, DOCX, XLSX" },
];

export function StatsBand() {
  return (
    <section className="py-16 lg:py-20">
      <div className="container-x">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {STATS.map((s, i) => (
            <Reveal key={s.label} delay={i * 0.07}>
              <SpotlightCard className="h-full p-6">
                <div className="serif text-[40px] leading-none text-[var(--ink)] lg:text-[48px]">{s.node}</div>
                <div className="mt-3 text-[15px] font-medium text-[var(--ink)]">{s.label}</div>
                <div className="mt-1 text-[13px] leading-[1.5] text-[var(--muted)]">{s.sub}</div>
              </SpotlightCard>
            </Reveal>
          ))}
        </div>
        <p className="mt-4 text-center text-[12px] text-[var(--muted)]">Measured on our own synthetic evaluation sets; treat as engineering benchmarks, not guarantees.</p>
      </div>
    </section>
  );
}
