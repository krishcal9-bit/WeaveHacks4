"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MotionConfig, motion, useReducedMotion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import { cx } from "@/components/ui";
import { useAppNavShortcuts } from "@/hooks/use-app-nav-shortcuts";
import { PageTransition } from "@/components/motion/page-transition";
import { pressSubtle, springSnappy, transitionReveal } from "@/components/motion/variants";
import { ThemeToggle } from "@/components/theme-toggle";
import { agentBase } from "@/lib/agent-base";
import { APP_NAME } from "@/lib/branding";
import { APP_NAV } from "@/lib/app-nav";

/*
  App chrome — the masthead of the financial press.

  Motion policy: `reducedMotion="user"` (NOT "always"). Animations are back on
  by design; responsiveness is protected by the motion contract in globals.css
  (compositor-only keyframes, scoped loops) and by the Decision Room's state
  throttling — not by disabling motion app-wide.
*/

type HealthState = "checking" | "ready" | "blocked";

function useSystemHealth(enabled: boolean): { state: HealthState; detail: string } {
  const [state, setState] = useState<HealthState>("checking");
  const [detail, setDetail] = useState("Checking live systems…");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const load = async () => {
      try {
        const res = await fetch(`${agentBase()}/api/health`, { cache: "no-store" });
        const data = (await res.json().catch(() => null)) as { ready?: boolean; blockers?: string[] } | null;
        if (cancelled) return;
        if (data?.ready) {
          setState("ready");
          setDetail("All live systems green — OpenAI, W&B Weave, Redis, CopilotKit");
        } else {
          setState("blocked");
          setDetail(data?.blockers?.[0] ?? "Strict-live preflight is blocked");
        }
      } catch {
        if (!cancelled) {
          setState("blocked");
          setDetail("Agent service unreachable");
        }
      }
    };

    void load();
    const interval = window.setInterval(load, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [enabled]);

  return { state, detail };
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === "/";
  const reduced = Boolean(useReducedMotion());
  const health = useSystemHealth(!isLanding);

  useAppNavShortcuts();

  if (isLanding) {
    return (
      <MotionConfig reducedMotion="user">
        <PageTransition pathname={pathname}>{children}</PageTransition>
      </MotionConfig>
    );
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="relative flex min-h-dvh flex-col bg-background md:h-dvh md:overflow-hidden">
        {/* Static atmosphere: vignette depth + paper grain. Painted once. */}
        <div className="app-vignette pointer-events-none fixed inset-0 z-40" aria-hidden />
        <div className="app-grain pointer-events-none fixed inset-0 z-50" aria-hidden />

        <header className="editorial-masthead sticky top-0 z-20 bg-surface/95 backdrop-blur">
          <div className="flex min-h-[3.4rem] flex-wrap items-center gap-3 border-b border-border/70 px-3 py-2 md:px-5">
            <motion.div whileHover={reduced ? undefined : { opacity: 0.84 }} whileTap={reduced ? undefined : pressSubtle} transition={transitionReveal}>
              <Link href="/" className="group flex items-center gap-2.5 transition-opacity hover:opacity-80">
                <AtlasMark reduced={reduced} />
                <span className="flex flex-col leading-none">
                  <span className="font-display text-[24px] font-medium leading-none tracking-[-0.02em] text-foreground">
                    {APP_NAME}
                  </span>
                  <span className="mt-1 hidden font-mono text-[8.5px] font-semibold uppercase tracking-[0.34em] text-gilt sm:block">
                    The AI Finance Desk
                  </span>
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
                      "relative flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full px-3 text-[12px] font-semibold transition-colors",
                      active ? "text-background" : "text-muted-foreground hover:text-foreground",
                    )}
                    title={item.label}
                  >
                    {active && (
                      <motion.span
                        layoutId="atlas-nav-pill"
                        className="absolute inset-0 rounded-full bg-foreground shadow-sm"
                        transition={reduced ? { duration: 0 } : springSnappy}
                      />
                    )}
                    <Icon className="relative z-10 h-3.5 w-3.5 shrink-0" strokeWidth={1.85} />
                    <span className="relative z-10 hidden sm:inline">{item.label}</span>
                  </Link>
                );
              })}
            </nav>

            <span
              title={health.detail}
              className={cx(
                "hidden h-8 shrink-0 items-center gap-2 rounded-full border px-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] md:inline-flex",
                health.state === "ready"
                  ? "border-positive/30 bg-positive-bg text-positive"
                  : health.state === "blocked"
                    ? "border-risk/30 bg-risk-bg text-risk"
                    : "border-border bg-background text-subtle-foreground",
              )}
            >
              <span className={health.state === "ready" ? "live-dot" : "status-dot status-dot--risk"} aria-hidden />
              {health.state === "ready" ? "Live" : health.state === "blocked" ? "Blocked" : "Checking"}
            </span>

            <ThemeToggle />
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
