"use client";

import { Activity, Fingerprint, Layers, Lock, ScanSearch, ShieldOff } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const POINTS = [
  { icon: ShieldOff, title: "Holds by default", body: "If the gateway is down, a detector fails or anything looks unsafe, the request is blocked. Nothing passes through unchecked.", wide: true },
  { icon: ScanSearch, title: "Checked twice", body: "An independent scanner that shares no patterns with the detector re-reads the masked output. Doubt blocks the request." },
  { icon: Layers, title: "Scoped to each conversation", body: "Every chat has its own encrypted mapping, kept for 24 hours. No cross-chat fingerprinting." },
  { icon: Lock, title: "Only our extension can call it", body: "Loopback only, a per-install token, a pinned extension ID and a Host check against DNS rebinding." },
  { icon: Activity, title: "Honest about coverage", body: "Every response says what was not checked, for example names while the semantic model warms up, and strict mode can block on it.", wide: true },
  { icon: Fingerprint, title: "Auditable by design", body: "Every demask call is logged with counts only. Admin actions, including deletions, are recorded." },
];

export function Difference() {
  return (
    <section id="security" className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="Security" title="Built like a security product." desc="Designed for teams who need to ship AI access without shipping data." />
        <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {POINTS.map((p, i) => {
            const Icon = p.icon;
            return (
              <Reveal key={p.title} delay={(i % 4) * 0.06} className={p.wide ? "lg:col-span-2" : ""}>
                <SpotlightCard className="h-full p-6">
                  <span className="grid h-11 w-11 place-items-center rounded-xl border border-[var(--border-strong)] text-[var(--primary)]"><Icon className="h-5 w-5" strokeWidth={2.25} /></span>
                  <h3 className="serif mt-5 text-[21px] text-[var(--ink)]">{p.title}</h3>
                  <p className="mt-2.5 text-[15px] leading-[1.65] text-[var(--body)]">{p.body}</p>
                </SpotlightCard>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
