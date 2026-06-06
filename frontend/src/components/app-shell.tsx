"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Database, Scale, Settings } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/components/ui";

const NAV = [
  { href: "/", label: "Data", icon: Database },
  { href: "/decisions", label: "Run", icon: Scale },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-dvh flex-col bg-background md:h-dvh md:overflow-hidden">
      <header className="sticky top-0 z-20 border-b border-border bg-surface/95 px-3 py-2 backdrop-blur md:px-4">
        <div className="flex min-h-12 flex-wrap items-center gap-2.5">
          <AtlasMark />
          <div className="min-w-0 leading-tight">
            <div className="truncate text-[16px] font-semibold">Atlas Finance OS</div>
            <div className="hidden truncate text-[10px] text-subtle-foreground sm:block">Upload files, then run the council</div>
          </div>

          <nav className="ml-auto flex max-w-full gap-1 overflow-x-auto rounded-full border border-border bg-background p-1">
            {NAV.map((item) => {
              const active = pathname === item.href;
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cx(
                    "flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium transition-colors",
                    active
                      ? "bg-foreground text-background shadow-sm"
                      : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                  )}
                  title={item.label}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={1.85} />
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </nav>

        </div>
      </header>

      <main className="min-w-0 flex-1 overflow-y-auto bg-background md:h-full">{children}</main>
    </div>
  );
}

function AtlasMark() {
  return (
    <svg className="h-7 w-7 shrink-0 text-foreground" viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M4.6 27.6 15.3 3.4c.35-.78 1.45-.78 1.79 0l10.31 23.9c.35.82-.52 1.62-1.49 1.08l-6.47-3.57H11.9l-6.03 3.53c-.91.53-1.63-.48-1.27-1.14Zm8.18-7.62h5.59l-2.76-8.04-2.83 8.04Z"
        fill="currentColor"
      />
    </svg>
  );
}
