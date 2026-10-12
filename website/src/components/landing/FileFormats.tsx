"use client";

import { Reveal } from "./Reveal";
import { SectionHeader, SpotlightCard } from "./bits";

const FORMATS = [
  {
    ext: "txt",
    name: "Plain text",
    desc: "TXT, MD, CSV, JSON — masked in place",
    supported: true,
  },
  {
    ext: "pdf",
    name: "PDF documents",
    desc: "Text redacted in place, layout preserved",
    supported: true,
  },
  {
    ext: "docx",
    name: "Word documents",
    desc: "Body, tables, headers — all scanned",
    supported: true,
  },
  {
    ext: "xlsx",
    name: "Excel spreadsheets",
    desc: "String + numeric cells, cleaned output",
    supported: true,
  },
  {
    ext: "img",
    name: "Images & scans",
    desc: "Blocked by default — no OCR yet",
    supported: false,
  },
  {
    ext: "?",
    name: "Unknown types",
    desc: "Blocked — never passed through",
    supported: false,
  },
];

export function FileFormats() {
  return (
    <section className="relative py-24 lg:py-32">
      <div className="container-x">
        <SectionHeader
          eyebrow="File uploads"
          title={<>Files keep their <span className="text-gradient">format</span>.</>}
          desc="Text in PDF and Word is masked in place. Excel files come back as a cleaned copy with sensitive cells swapped. Scanned PDFs and images are blocked, not guessed at."
        />

        <div className="mt-14 grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {FORMATS.map((f, i) => (
            <Reveal key={f.name} delay={i * 0.06}>
              <SpotlightCard className="h-full p-6">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <span
                      className={`grid h-11 w-11 place-items-center rounded-xl border text-[15px] font-bold ${
                        f.supported
                          ? "border-[var(--border-strong)] bg-[var(--subtle)] text-[var(--primary)]"
                          : "border-[var(--border)] bg-transparent text-[var(--muted)]"
                      }`}
                    >
                      {f.ext}
                    </span>
                    <div>
                      <div className="text-[15px] font-medium text-[var(--ink)]">{f.name}</div>
                      <div className="text-[13px] text-[var(--muted)] mt-0.5">{f.desc}</div>
                    </div>
                  </div>
                  {f.supported ? (
                    <span className="rounded-full bg-[rgba(47,213,143,.12)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--primary)]">
                      Supported
                    </span>
                  ) : (
                    <span className="rounded-full bg-[rgba(224,96,91,.12)] px-2.5 py-0.5 text-[11px] font-semibold text-[var(--danger)]">
                      Blocked
                    </span>
                  )}
                </div>
              </SpotlightCard>
            </Reveal>
          ))}
        </div>

        <p className="mt-6 text-[13px] text-[var(--muted)] max-w-[640px]">
          File type is detected from magic bytes, not the extension. A .docx renamed as .txt is still processed as Word.
        </p>
      </div>
    </section>
  );
}
