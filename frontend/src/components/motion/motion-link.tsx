"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import type { ComponentProps, ReactNode } from "react";
import { useMounted } from "@/lib/use-mounted";

type MotionLinkProps = ComponentProps<typeof Link> & {
  children: ReactNode;
  variant?: "default" | "landing-cta" | "landing-ghost";
};

export function MotionLink({ children, className, variant = "default", ...props }: MotionLinkProps) {
  const mounted = useMounted();
  const reduced = useReducedMotion();

  if (!mounted || reduced) {
    return (
      <Link className={className} {...props}>
        {children}
      </Link>
    );
  }

  return (
    <motion.div
      className="inline-flex"
      whileHover={variant === "landing-cta" ? { scale: 1.03, y: -1 } : { scale: 1.02 }}
      whileTap={{ scale: 0.97 }}
      transition={{ type: "spring", stiffness: 480, damping: 28 }}
    >
      <Link className={className} {...props}>
        {children}
      </Link>
    </motion.div>
  );
}
