"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Menu, ShieldCheck, X } from "lucide-react";
import { CTA_HREF, CTA_LABEL } from "@/lib/site";

const LINKS = [
  { label: "How it works", id: "how-it-works" },
  { label: "Coverage", id: "coverage" },
  { label: "Platform", id: "platform" },
  { label: "Security", id: "security" },
  { label: "FAQ", id: "faq" },
];

export function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("");

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && setActive(e.target.id)),
      { rootMargin: "-45% 0px -50% 0px" }
    );
    LINKS.forEach((l) => {
      const el = document.getElementById(l.id);
      if (el) io.observe(el);
    });
    return () => {
      window.removeEventListener("scroll", onScroll);
      io.disconnect();
    };
  }, []);

  return (
    <header className={`fixed inset-x-0 top-0 z-50 transition-all duration-300 ${scrolled ? "glass border-b border-[var(--border)]" : "bg-transparent"}`}>
      <div className="container-x">
        <div className="flex h-16 items-center justify-between">
          <a href="#top" className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-[var(--primary)] shadow-[0_0_24px_-4px_rgba(47,213,143,.7)]">
              <ShieldCheck className="h-5 w-5 text-[var(--bg)]" strokeWidth={2.5} />
            </span>
            <span className="serif text-[18px] text-[var(--ink)]">Doppel</span>
          </a>

          <nav className="hidden items-center gap-1 lg:flex">
            {LINKS.map((l) => (
              <a
                key={l.id}
                href={`#${l.id}`}
                className={`rounded-full px-4 py-2 text-[14px] font-medium transition-colors ${
                  active === l.id ? "bg-white/[0.06] text-[var(--ink)]" : "text-[var(--body)] hover:text-[var(--ink)]"
                }`}
              >
                {l.label}
              </a>
            ))}
          </nav>

          <div className="hidden lg:block">
            <a href={CTA_HREF} className="btn-primary !px-5 !py-2.5 !text-[14px]">
              {CTA_LABEL}
              <ArrowRight className="h-4 w-4" />
            </a>
          </div>

          <button
            type="button"
            aria-label="Toggle menu"
            onClick={() => setOpen((v) => !v)}
            className="grid h-10 w-10 place-items-center rounded-full border border-[var(--border-strong)] bg-[var(--surface)] text-[var(--ink)] lg:hidden"
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </div>

      {open && (
        <div className="glass border-t border-[var(--border)] lg:hidden">
          <div className="container-x flex flex-col gap-1 py-4">
            {LINKS.map((l) => (
              <a key={l.id} href={`#${l.id}`} onClick={() => setOpen(false)} className="rounded-lg px-3.5 py-3 text-[15px] font-medium text-[var(--ink)] hover:bg-white/[0.05]">
                {l.label}
              </a>
            ))}
            <a href={CTA_HREF} onClick={() => setOpen(false)} className="btn-primary mt-2">{CTA_LABEL}</a>
          </div>
        </div>
      )}
    </header>
  );
}
