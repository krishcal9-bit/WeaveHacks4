"use client";

import { motion } from "motion/react";
import { Moon, Sun } from "lucide-react";
import { cx } from "@/components/ui";
import { springSnappy } from "@/components/motion/variants";
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
      ? "border-white/12 bg-white/[0.04] text-[#d8d0c4] hover:border-white/20 hover:bg-white/[0.08] hover:text-[#f6f0e6]"
      : "border-border bg-background text-muted-foreground hover:bg-surface-muted hover:text-foreground",
    className,
  );

export function ThemeToggle({ className, variant = "app" }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  const mounted = useMounted();
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
      whileHover={{ scale: 1.08, rotate: 12 }}
      whileTap={{ scale: 0.92 }}
      transition={springSnappy}
    >
      <motion.span key={isDark ? "sun" : "moon"} initial={false} animate={{ rotate: 0, opacity: 1 }} transition={{ duration: 0.28 }}>
        {icon}
      </motion.span>
    </motion.button>
  );
}
