import { Navbar } from "@/components/landing/Navbar";
import { ScrollProgress } from "@/components/landing/ScrollProgress";
import { Hero } from "@/components/landing/Hero";
import { Pillars } from "@/components/landing/Pillars";
import { Difference } from "@/components/landing/Difference";
import { PIITypes } from "@/components/landing/PIITypes";
import { FileFormats } from "@/components/landing/FileFormats";
import { Architecture } from "@/components/landing/Architecture";
import { Testimonials } from "@/components/landing/Testimonials";
import { FAQ } from "@/components/landing/FAQ";
import { CTA } from "@/components/landing/CTA";
import { Footer } from "@/components/landing/Footer";

export default function Page() {
  return (
    <main className="relative min-h-screen flex flex-col bg-[var(--bg)] text-[var(--ink)]">
      <ScrollProgress />
      <Navbar />
      <div className="flex-1">
        <Hero />
        <Pillars />
        <Difference />
        <PIITypes />
        <FileFormats />
        <Architecture />
        <Testimonials />
        <FAQ />
        <CTA />
      </div>
      <Footer />
    </main>
  );
}
