"use client";

import { motion, useScroll, useSpring } from "framer-motion";

export function ScrollProgress() {
  const { scrollYProgress } = useScroll();
  const scaleX = useSpring(scrollYProgress, { stiffness: 140, damping: 30, mass: 0.3 });
  return (
    <motion.div
      className="pointer-events-none fixed left-0 right-0 top-0 z-[60] h-[2px] origin-left"
      style={{ scaleX, background: "linear-gradient(90deg, var(--primary), var(--teal))" }}
    />
  );
}
