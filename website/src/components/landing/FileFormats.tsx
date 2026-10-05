"use client";

import { motion } from "framer-motion";
import { FileText, FileType, FileCode, Sheet } from "lucide-react";
import { Reveal } from "./Reveal";

const FORMATS = [
  { ext: "Plain text", name: "TXT · MD · CSV", icon: FileText, color: "#2FD58F" },
  { ext: "PDF documents", name: "PDF", icon: FileType, color: "#2FD58F" },
  { ext: "Word documents", name: "DOCX", icon: FileCode, color: "#2FD58F" },
  { ext: "Spreadsheets", name: "XLSX", icon: Sheet, color: "#2FD58F" },
];

export function FileFormats() {
  return (
    <section className="relative py-24 lg:py-32">
      <div className="absolute inset-0 -z-10 bg-grid-dot opacity-40" />

      <div className="mx-auto max-w-[1248px] px-5 lg:px-8">
        <div className="max-w-[640px]">
          <Reveal>
            <h2 className="text-[36px] lg:text-[48px] font-semibold serif tracking-[-0.02em] leading-[1.08] text-[var(--ink)]">
              Files keep their format.
            </h2>
            <p className="mt-4 text-[17px] leading-[1.55] text-[var(--mist-dim)] font-medium">
              Layout, images, formulas, and formatting are all preserved. The file your team uploaded is the same file the AI tool sees — just with sensitive values swapped.
            </p>
          </Reveal>
        </div>

        <div className="mt-14 grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {FORMATS.map((f, i) => {
            const Icon = f.icon;
            return (
              <motion.div
                key={f.ext}
                initial={{ opacity: 0, y: 20, rotate: i % 2 === 0 ? -1.5 : 1.5 }}
                whileInView={{ opacity: 1, y: 0, rotate: i % 2 === 0 ? -1.5 : 1.5 }}
                viewport={{ once: true, margin: "-80px" }}
                transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
                className="refined-card rounded-2xl p-6 text-center"
                style={{ transform: `rotate(${i % 2 === 0 ? -1.5 : 1.5}deg)` }}
              >
                <span
                  className="grid place-items-center h-12 w-12 rounded-xl border border-[var(--border)] mx-auto"
                  style={{ background: f.color, boxShadow: "0 6px 16px -6px rgba(47, 213, 143, 0.25)" }}
                >
                  <Icon className="h-6 w-6 text-[var(--ink)]" strokeWidth={2.5} />
                </span>
                <div className="mt-4 text-[15px] font-bold text-[var(--ink)]">
                  {f.ext}
                </div>
                <div className="text-[12px] mono-code text-[var(--mist-dim)] mt-1">
                  {f.name}
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
