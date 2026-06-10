"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MotionConfig, motion, useReducedMotion } from "motion/react";
import { LayoutGrid } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/components/ui";
import { useAppNavShortcuts } from "@/hooks/use-app-nav-shortcuts";
import { PageTransition } from "@/components/motion/page-transition";
import { hoverLift, pressSubtle, springSnappy, transitionReveal } from "@/components/motion/variants";
import { ThemeToggle } from "@/components/theme-toggle";
import { APP_NAME } from "@/lib/branding";
import { APP_NAV } from "@/lib/app-nav";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === "/";
  const reduced = Boolean(useReducedMotion());

  useAppNavShortcuts();

  // Global motion kill-switch: force reduced motion app-wide so every
  // framer-motion component (all of which already guard on `useReducedMotion`)
  // skips its transform/layout animations. Combined with the CSS animation
  // kill-switch in globals.css, this keeps the heavy live-streaming Decision
  // Room responsive instead of repainting animations on every state delta.
  if (isLanding) {
    return (
      <MotionConfig reducedMotion="always">
        <PageTransition pathname={pathname}>{children}</PageTransition>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="always">
    <div className="relative flex min-h-dvh flex-col bg-background md:h-dvh md:overflow-hidden">
      <div className="app-grain pointer-events-none fixed inset-0 z-50" aria-hidden />

      <header className="editorial-masthead sticky top-0 z-20 bg-surface/95 backdrop-blur">
        <div className="flex min-h-[3.25rem] flex-wrap items-center gap-3 border-b border-border/70 px-3 py-2 md:px-5">
          <motion.div whileHover={reduced ? undefined : { opacity: 0.84 }} whileTap={reduced ? undefined : pressSubtle} transition={transitionReveal}>
            <Link href="/" className="flex items-baseline gap-2.5 transition-opacity hover:opacity-80">
              <AtlasMark reduced={reduced} />
              <span className="font-display text-[26px] font-medium leading-none tracking-[-0.02em] text-foreground">
                {APP_NAME}
              </span>
            </Link>
          </motion.div>

          <nav className="ml-auto flex max-w-full gap-1 overflow-x-auto rounded-full border border-border bg-background p-1">
            {APP_NAV.map((item) => {
              const active =
                pathname === item.href || (item.href === "/dashboard" && pathname.startsWith("/dashboard"));
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cx(
                    "relative flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium transition-colors",
                    active ? "text-background" : "text-muted-foreground hover:text-foreground",
                  )}
                  title={item.label}
                >
                  {active && (
                    <motion.span
                      layoutId="atlas-nav-pill"
                      className="absolute inset-0 rounded-full bg-foreground shadow-sm"
                      transition={springSnappy}
                    />
                  )}
                  <Icon className="relative z-10 h-3.5 w-3.5 shrink-0" strokeWidth={1.85} />
                  <span className="relative z-10 hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <ThemeToggle />
          <motion.div whileHover={reduced ? undefined : hoverLift} whileTap={reduced ? undefined : pressSubtle} transition={springSnappy}>
            <Link
              href="/"
              className="hidden h-8 items-center gap-1.5 rounded-full border border-border px-3 text-[11px] font-semibold text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground sm:inline-flex"
              title="Back to marketing site"
            >
              <LayoutGrid className="h-3.5 w-3.5" strokeWidth={1.85} />
              Home
            </Link>
          </motion.div>
        </div>
      </header>

      <main className="relative min-w-0 flex-1 overflow-y-auto bg-background md:h-full">
        <PageTransition pathname={pathname}>{children}</PageTransition>
      </main>
    </div>
    </MotionConfig>
  );
}

function AtlasMark({ reduced }: { reduced: boolean }) {
  return (
    <motion.svg
      className="h-7 w-7 shrink-0 text-accent"
      viewBox="0 0 32 32"
      aria-hidden="true"
      whileHover={reduced ? undefined : { rotate: [0, -5, 5, 0], transition: transitionReveal }}
    >
      <path
        d="M4.6 27.6 15.3 3.4c.35-.78 1.45-.78 1.79 0l10.31 23.9c.35.82-.52 1.62-1.49 1.08l-6.47-3.57H11.9l-6.03 3.53c-.91.53-1.63-.48-1.27-1.14Zm8.18-7.62h5.59l-2.76-8.04-2.83 8.04Z"
        fill="currentColor"
      />
    </motion.svg>
  );
}
