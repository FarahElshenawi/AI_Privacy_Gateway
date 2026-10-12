import { Navbar } from "@/components/landing/Navbar";
import { ScrollProgress } from "@/components/landing/ScrollProgress";
import { Hero } from "@/components/landing/Hero";
import { Marquee } from "@/components/landing/Marquee";
import { StatsBand } from "@/components/landing/StatsBand";
import { Pillars } from "@/components/landing/Pillars";
import { PIITypes } from "@/components/landing/PIITypes";
import { DashboardPreview } from "@/components/landing/DashboardPreview";
import { Architecture } from "@/components/landing/Architecture";
import { Difference } from "@/components/landing/Difference";
import { FileFormats } from "@/components/landing/FileFormats";
import { FAQ } from "@/components/landing/FAQ";
import { CTA } from "@/components/landing/CTA";
import { Footer } from "@/components/landing/Footer";

export default function Page() {
  return (
    <main className="relative flex min-h-screen flex-col bg-[var(--bg)] text-[var(--ink)]">
      <ScrollProgress />
      <Navbar />
      <div className="flex-1">
        <Hero />
        <Marquee />
        <StatsBand />
        <Pillars />
        <PIITypes />
        <DashboardPreview />
        <Architecture />
        <Difference />
        <FileFormats />
        <FAQ />
        <CTA />
      </div>
      <Footer />
    </main>
  );
}
