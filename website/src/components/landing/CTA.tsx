"use client";

import { ArrowRight } from "lucide-react";
import { CTA_HREF, CTA_LABEL } from "@/lib/site";
import { Reveal } from "./Reveal";

export function CTA() {
  return (
    <section className="relative overflow-hidden py-24 lg:py-32">
      <div className="container-x">
        <Reveal>
          <div className="relative overflow-hidden rounded-[28px] border border-[rgba(47,213,143,.35)] bg-[var(--surface)] px-6 py-16 text-center sm:px-14 lg:py-24">
            <div className="aurora"><i /><i /><i /></div>
            <div className="bg-grid absolute inset-0" />
            <div className="relative">
              <h2 className="serif mx-auto max-w-[760px] text-[38px] leading-[1.05] text-[var(--ink)] sm:text-[52px] lg:text-[66px]">
                Let your teams use AI. <span className="text-gradient">Keep your data.</span>
              </h2>
              <p className="mx-auto mt-6 max-w-[520px] text-[18px] leading-[1.55] text-[var(--body)]">Deploy in a day. Watch Doppel protect your first prompt in minutes.</p>
              <div className="mt-10 flex justify-center">
                <a href={CTA_HREF} className="btn-primary !px-8 !py-4 !text-[16px]">{CTA_LABEL}<ArrowRight className="h-4 w-4" /></a>
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
