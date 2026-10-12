"use client";

import { ShieldCheck, Github } from "lucide-react";
import { REPO_URL } from "@/lib/site";

export function Footer() {
  return (
    <footer className="relative mt-auto border-t border-[var(--border)] bg-[var(--surface)]/50">
      <div className="container-x py-14">
        <div className="grid gap-10 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">
          {/* Brand */}
          <div>
            <a href="#top" className="flex items-center gap-2.5">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-[var(--primary)]">
                <ShieldCheck className="h-5 w-5 text-[var(--bg)]" strokeWidth={2.5} />
              </span>
              <span className="serif text-[15px] font-medium tracking-tight text-[var(--ink)]">
                Doppel
              </span>
            </a>
            <p className="mt-5 max-w-[280px] text-[14px] leading-[1.6] text-[var(--body)]">
              The privacy layer for the AI tools your teams already use.
            </p>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener"
              className="mt-4 inline-flex items-center gap-2 text-[13px] text-[var(--muted)] transition-colors hover:text-[var(--primary)]"
            >
              <Github className="h-4 w-4" />
              GitHub
            </a>
          </div>

          {/* Link columns */}
          {[
            { title: "Product", links: [{ label: "How it works", href: "#how-it-works" }, { label: "Coverage", href: "#coverage" }, { label: "Platform", href: "#platform" }, { label: "Security", href: "#security" }] },
            { title: "Resources", links: [{ label: "Documentation", href: REPO_URL }, { label: "Security model", href: "#security" }, { label: "FAQ", href: "#faq" }] },
            { title: "Open source", links: [{ label: "GitHub", href: REPO_URL }, { label: "License (MIT)", href: REPO_URL + "/blob/develop/LICENSE" }, { label: "Changelog", href: REPO_URL + "/blob/develop/CHANGELOG.md" }] },
          ].map((col) => (
            <div key={col.title}>
              <div className="eyebrow mb-3.5">{col.title}</div>
              <ul className="space-y-2.5">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <a
                      href={l.href}
                      className="text-[13.5px] text-[var(--body)] transition-colors hover:text-[var(--primary)]"
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-t border-[var(--border)] pt-6">
          <div className="text-[12px] text-[var(--muted)]">
            © {new Date().getFullYear()} Doppel — AI Privacy Gateway. Open source under MIT.
          </div>
          <div className="flex items-center gap-4 text-[12px] text-[var(--muted)]">
            <a href="#security" className="transition-colors hover:text-[var(--primary)]">Privacy</a>
            <a href={REPO_URL + "/blob/develop/LICENSE"} target="_blank" rel="noopener" className="transition-colors hover:text-[var(--primary)]">License</a>
            <a href="#faq" className="transition-colors hover:text-[var(--primary)]">Security</a>
          </div>
        </div>
      </div>
    </footer>
  );
}
