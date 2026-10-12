import { ShieldCheck } from "lucide-react";
import { REPO_URL } from "@/lib/site";

const COLUMNS = [
  { title: "Product", links: [["How it works", "#how-it-works"], ["Coverage", "#coverage"], ["Platform", "#platform"], ["Architecture", "#architecture"]] },
  { title: "Trust", links: [["Security", "#security"], ["FAQ", "#faq"], ["Source code", REPO_URL]] },
];

export function Footer() {
  return (
    <footer className="relative mt-auto border-t border-[var(--border)] bg-[var(--surface)]/50">
      <div className="container-x py-14">
        <div className="grid gap-10 lg:grid-cols-[1.6fr_1fr_1fr]">
          <div>
            <a href="#top" className="flex items-center gap-2.5">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-[var(--primary)]"><ShieldCheck className="h-5 w-5 text-[var(--bg)]" strokeWidth={2.5} /></span>
              <span className="serif text-[18px] text-[var(--ink)]">Doppel</span>
            </a>
            <p className="mt-5 max-w-[320px] text-[14px] leading-[1.6] text-[var(--body)]">The privacy layer for the AI tools your teams already use. Looks like your data. Acts like your data. Never is your data.</p>
          </div>
          {COLUMNS.map((c) => (
            <div key={c.title}>
              <div className="mb-4 text-[12px] uppercase tracking-[0.18em] text-[var(--muted)]">{c.title}</div>
              <ul className="space-y-2.5">
                {c.links.map(([l, h]) => (
                  <li key={l}><a href={h} className="text-[14px] text-[var(--body)] transition-colors hover:text-[var(--primary)]">{l}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 flex flex-col justify-between gap-3 border-t border-[var(--border)] pt-6 text-[12px] text-[var(--muted)] sm:flex-row">
          <span>© {new Date().getFullYear()} Doppel. All rights reserved.</span>
          <span>Built to support GDPR, HIPAA, PCI-DSS and SOC 2 programs. Not a certification.</span>
        </div>
      </div>
    </footer>
  );
}
