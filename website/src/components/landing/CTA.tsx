"use client";

import { motion } from "framer-motion";
import { ArrowRight, Github } from "lucide-react";
import { CTA_HREF, CTA_LABEL, REPO_URL } from "@/lib/site";
import { Reveal } from "./Reveal";

export function CTA() {
  return (
    <section className="relative py-24 lg:py-32 overflow-hidden">
      <div className="absolute inset-0 -z-10 bg-grid opacity-50" />
      <div
        className="absolute inset-0 -z-10"
        style={{
          background:
            "radial-gradient(50% 50% at 50% 50%, rgba(47,213,143,0.12) 0%, transparent 70%)",
        }}
      />

      <div className="container-x">
        <Reveal>
          <div className="panel p-10 sm:p-14 lg:p-20 text-center">
            <h2 className="serif mx-auto max-w-[760px] text-[36px] leading-[1.05] text-[var(--ink)] sm:text-[48px] lg:text-[64px]">
              Let your teams use AI.
              <br />
              <span className="text-gradient">Keep your data.</span>
            </h2>

            <p className="mx-auto mt-6 max-w-[480px] text-[18px] leading-[1.5] text-[var(--body)]">
              Deploy in a day. See the gateway protect your first prompt in minutes.
            </p>

            <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-3">
              <motion.a
                href={CTA_HREF}
                whileHover={{ y: -2 }}
                className="btn-primary w-full sm:w-auto"
              >
                <span>{CTA_LABEL}</span>
                <ArrowRight className="h-4 w-4" />
              </motion.a>
              <motion.a
                href={REPO_URL}
                target="_blank"
                rel="noopener"
                whileHover={{ y: -2 }}
                className="btn-ghost w-full sm:w-auto"
              >
                <Github className="h-4 w-4" />
                <span>View the code</span>
              </motion.a>
            </div>

            <p className="mt-8 text-[13px] text-[var(--muted)]">
              Open source · MIT License · Runs locally on your machine
            </p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
