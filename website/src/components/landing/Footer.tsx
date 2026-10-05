"use client";

import { ShieldCheck } from "lucide-react";

const COLUMNS = [
  {
    title: "Product",
    links: ["How it works", "Coverage", "Architecture", "Customers"],
  },
  {
    title: "Resources",
    links: ["Documentation", "Security", "Trust center", "API reference"],
  },
  {
    title: "Company",
    links: ["About", "Customers", "Careers", "Contact"],
  },
];

export function Footer() {
  return (
    <footer className="relative mt-auto border-t border-[var(--ink)]/[0.10] bg-[var(--surface)]/50">
      <div className="absolute inset-0 -z-10 bg-grid-faint opacity-30" />
      <div className="mx-auto max-w-[1248px] px-5 lg:px-8 py-14">
        <div className="grid gap-10 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">
          <div>
            <a href="#top" className="flex items-center gap-2.5">
              <span
                className="grid place-items-center h-9 w-9 rounded-xl"
                style={{
                  background: "var(--primary)",
                  border: "1px solid var(--primary)",
                }}
              >
                <ShieldCheck className="h-5 w-5 text-[var(--bg)]" strokeWidth={2.5} />
              </span>
              <span className="text-[15px] font-medium tracking-tight text-[var(--ink)] serif">
                Doppel
              </span>
            </a>
            <p className="mt-5 text-[14px] leading-[1.6] text-[var(--mist-dim)] max-w-[280px]">
              The privacy layer for the AI tools your teams already use.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.title}>
              <div className="text-[11px] mono-code uppercase tracking-[0.18em] text-[var(--mist-dim)] mb-3.5">
                {col.title}
              </div>
              <ul className="space-y-2.5">
                {col.links.map((l) => (
                  <li key={l}>
                    <a
                      href="#"
                      className="text-[13.5px] text-[var(--mist-dim)] hover:text-[var(--lime)] transition-colors"
                    >
                      {l}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 pt-6 border-t border-[var(--ink)]/[0.10] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div className="text-[12px] text-[var(--mist-dim)]">
            © {new Date().getFullYear()} Doppel. All rights reserved.
          </div>
          <div className="flex items-center gap-4 text-[12px] text-[var(--mist-dim)]">
            <a href="#" className="hover:text-[var(--lime)] transition-colors">Privacy</a>
            <a href="#" className="hover:text-[var(--lime)] transition-colors">Terms</a>
            <a href="#" className="hover:text-[var(--lime)] transition-colors">Security</a>
          </div>
        </div>
      </div>
    </footer>
  );
}
