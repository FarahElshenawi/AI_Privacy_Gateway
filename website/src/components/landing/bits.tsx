"use client";

import { animate, useInView } from "framer-motion";
import { MouseEvent, ReactNode, useEffect, useRef, useState } from "react";
import { Reveal } from "./Reveal";

export function SpotlightCard({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const onMove = (e: MouseEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return (
    <div ref={ref} onMouseMove={onMove} className={`spot-card ${className}`}>
      {children}
    </div>
  );
}

export function CountUp({ to, decimals = 0, suffix = "", prefix = "" }: { to: number; decimals?: number; suffix?: string; prefix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!inView) return;
    const c = animate(0, to, { duration: 1.6, ease: [0.16, 1, 0.3, 1], onUpdate: setV });
    return () => c.stop();
  }, [inView, to]);
  return <span ref={ref} className="tabular-nums">{prefix}{v.toFixed(decimals)}{suffix}</span>;
}

export function SectionHeader({ eyebrow, title, desc, center = false }: { eyebrow: string; title: ReactNode; desc?: string; center?: boolean }) {
  return (
    <Reveal className={center ? "mx-auto max-w-[720px] text-center" : "max-w-[720px]"}>
      <span className="eyebrow">{eyebrow}</span>
      <h2 className="serif mt-3 text-[34px] leading-[1.1] text-[var(--ink)] sm:text-[44px] lg:text-[52px]">{title}</h2>
      {desc && <p className="mt-5 text-[17px] leading-[1.65] text-[var(--body)]">{desc}</p>}
    </Reveal>
  );
}
