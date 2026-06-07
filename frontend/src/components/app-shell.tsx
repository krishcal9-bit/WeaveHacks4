"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { LayoutGrid } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/components/ui";
import { useAppNavShortcuts } from "@/hooks/use-app-nav-shortcuts";
import { PageTransition } from "@/components/motion/page-transition";
import { ThemeToggle } from "@/components/theme-toggle";
import { APP_NAV } from "@/lib/app-nav";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === "/";

  useAppNavShortcuts();

  if (isLanding) {
    return <PageTransition pathname={pathname}>{children}</PageTransition>;
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background md:h-dvh md:overflow-hidden">
      <header className="sticky top-0 z-20 border-b border-border bg-surface/95 px-3 py-2 backdrop-blur md:px-4">
        <div className="flex min-h-12 flex-wrap items-center gap-2.5">
          <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
            <Link href="/" className="flex items-center gap-2 transition-opacity hover:opacity-80">
              <AtlasMark />
              <div className="min-w-0 leading-tight">
                <div className="truncate font-display text-[16px] font-medium tracking-tight">Atlas</div>
                <div className="hidden truncate font-mono text-[10px] text-subtle-foreground sm:block">finance os</div>
              </div>
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
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    />
                  )}
                  <Icon className="relative z-10 h-3.5 w-3.5 shrink-0" strokeWidth={1.85} />
                  <span className="relative z-10 hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <ThemeToggle />
          <motion.div whileHover={{ scale: 1.03 }} whileTap={{ scale: 0.97 }}>
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

      <main className="min-w-0 flex-1 overflow-y-auto bg-background md:h-full">
        <PageTransition pathname={pathname}>{children}</PageTransition>
      </main>
    </div>
  );
}

function AtlasMark() {
  return (
    <motion.svg
      className="h-7 w-7 shrink-0 text-accent"
      viewBox="0 0 32 32"
      aria-hidden="true"
      whileHover={{ rotate: [0, -6, 6, 0], transition: { duration: 0.45 } }}
    >
      <path
        d="M4.6 27.6 15.3 3.4c.35-.78 1.45-.78 1.79 0l10.31 23.9c.35.82-.52 1.62-1.49 1.08l-6.47-3.57H11.9l-6.03 3.53c-.91.53-1.63-.48-1.27-1.14Zm8.18-7.62h5.59l-2.76-8.04-2.83 8.04Z"
        fill="currentColor"
      />
    </motion.svg>
  );
}
