"use client";

import { FileSpreadsheet, FileText, FileType, ImageOff, Table2 } from "lucide-react";
import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const FORMATS = [
  { icon: FileText, name: "Text, CSV, TSV", body: "Encoding, BOM and line endings preserved. CSV columns use their header as context." },
  { icon: FileType, name: "PDF", body: "Character-level redaction: only the detected glyphs are removed and replaced in place." },
  { icon: FileText, name: "Word (.docx)", body: "Bodies, tables, headers, footers and hyperlinks, with run formatting kept." },
  { icon: FileSpreadsheet, name: "Excel (.xlsx)", body: "Strings, numeric cells, formulas, comments and hidden sheets. Returned as a cleaned copy." },
];

export function FileFormats() {
  return (
    <section className="relative border-t border-[var(--border)] py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader eyebrow="Files" title="Files keep their format." desc="Attachments are masked before upload and stay usable. Anything that cannot be inspected is blocked rather than guessed at." />
        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {FORMATS.map((f, i) => {
            const Icon = f.icon;
            return (
              <Reveal key={f.name} delay={i * 0.07}>
                <SpotlightCard className="h-full p-6">
                  <Icon className="h-6 w-6 text-[var(--primary)]" strokeWidth={2} />
                  <h3 className="mt-4 text-[17px] font-semibold text-[var(--ink)]">{f.name}</h3>
                  <p className="mt-2 text-[14px] leading-[1.65] text-[var(--body)]">{f.body}</p>
                </SpotlightCard>
              </Reveal>
            );
          })}
        </div>
        <Reveal delay={0.1}>
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-2xl border border-[rgba(224,96,91,.25)] bg-[rgba(224,96,91,.05)] px-6 py-4 text-[14px] text-[var(--body)]">
            <span className="flex items-center gap-2 font-semibold text-[var(--danger)]"><ImageOff className="h-4 w-4" /> Blocked, not passed through</span>
            <span>Scanned PDFs and images (no OCR)</span>
            <span>Tracked deletions and comments</span>
            <span>Encrypted or macro-enabled files</span>
            <span className="flex items-center gap-1.5"><Table2 className="h-3.5 w-3.5" /> Over 50 MB</span>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
