"use client";

import { useEffect, useState } from "react";
import { ShieldCheck, Menu, X } from "lucide-react";
import { CTA_HREF, CTA_LABEL } from "@/lib/site";

const NAV_LINKS = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Coverage", href: "#coverage" },
  { label: "Architecture", href: "#architecture" },
];

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={[
        "fixed top-0 left-0 right-0 z-50 transition-all duration-300",
        scrolled
          ? "bg-[var(--bg)]/90 backdrop-blur-xl border-b border-[var(--border)]"
          : "bg-transparent",
      ].join(" ")}
    >
      <div className="mx-auto max-w-[1120px] px-5 lg:px-8">
        <div className="flex h-16 items-center justify-between">
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
            <span className="text-[16px] font-medium tracking-tight text-[var(--ink)] serif">
              Doppel
            </span>
          </a>

          <nav className="hidden lg:flex items-center gap-7">
            {NAV_LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="nav-link text-[14px] font-medium"
              >
                {l.label}
              </a>
            ))}
          </nav>

          {/* Plain text link — keeps the rule "one green button per screen" (the hero's button) */}
          <div className="hidden lg:flex items-center">
            <a href={CTA_HREF} className="text-link text-[14px]">
              <span>{CTA_LABEL}</span>
            </a>
          </div>

          <button
            type="button"
            aria-label="Toggle menu"
            onClick={() => setOpen((v) => !v)}
            className="lg:hidden grid place-items-center h-10 w-10 rounded-full border border-[var(--border)] text-[var(--ink)] bg-[var(--surface)] hover:border-[var(--ink)] transition-colors"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {open && (
        <div className="lg:hidden border-t border-[var(--border)] bg-[var(--bg)]">
          <div className="mx-auto max-w-[1120px] px-5 py-4 flex flex-col gap-1">
            {NAV_LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="px-3.5 py-2.5 text-[15px] font-medium text-[var(--ink)] rounded-lg hover:bg-[var(--subtle)] transition-colors"
              >
                {l.label}
              </a>
            ))}
            <a
              href={CTA_HREF}
              onClick={() => setOpen(false)}
              className="btn-lime mt-2 px-5 py-3 text-center text-[15px]"
            >
              {CTA_LABEL}
            </a>
          </div>
        </div>
      )}
    </header>
  );
}
