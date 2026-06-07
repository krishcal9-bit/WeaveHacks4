"use client";

import { motion, useReducedMotion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { cx } from "@/components/ui";
import { pressTap, springSnappy, transitionReduced, transitionReveal } from "@/components/motion/variants";
import { useMounted } from "@/lib/use-mounted";
import { useTheme } from "@/components/theme-provider";

type ThemeToggleProps = {
  className?: string;
  variant?: "app" | "landing";
};

const toggleClass = (variant: "app" | "landing", className?: string) =>
  cx(
    "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors",
    variant === "landing"
      ? "border-border bg-surface text-muted-foreground hover:border-border-strong hover:bg-surface-muted hover:text-foreground"
      : "border-border bg-background text-muted-foreground hover:bg-surface-muted hover:text-foreground",
    className,
  );

export function ThemeToggle({ className, variant = "app" }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const mounted = useMounted();
  const reduced = Boolean(useReducedMotion());
  const isDark = theme === "dark";

  const handleClick = () => toggleTheme();

  const icon = mounted ? (isDark ? <Sun className="h-4 w-4" strokeWidth={1.85} /> : <Moon className="h-4 w-4" strokeWidth={1.85} />) : <Moon className="h-4 w-4" strokeWidth={1.85} />;
  const ariaLabel = mounted ? (isDark ? "Switch to light mode" : "Switch to dark mode") : "Toggle color theme";

  if (!mounted) {
    return (
      <button type="button" onClick={handleClick} aria-label={ariaLabel} title="Theme" className={toggleClass(variant, className)}>
        {icon}
      </button>
    );
  }

  return (
    <motion.button
      type="button"
      onClick={handleClick}
      aria-label={ariaLabel}
      title={isDark ? "Light mode" : "Dark mode"}
      className={toggleClass(variant, className)}
      whileHover={reduced ? undefined : { rotate: 10 }}
      whileTap={reduced ? undefined : pressTap}
      transition={springSnappy}
    >
      <motion.span key={isDark ? "sun" : "moon"} initial={false} animate={{ rotate: 0, opacity: 1 }} transition={reduced ? transitionReduced : transitionReveal}>
        {icon}
      </motion.span>
    </motion.button>
  );
}
