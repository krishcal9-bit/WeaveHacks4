"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  Database,
  FileText,
  LayoutDashboard,
  Network,
  Puzzle,
  Scale,
  Settings,
  ShieldCheck,
  Users,
  Workflow,
} from "lucide-react";
import type { ReactNode } from "react";
import { cx, StatusDot } from "@/components/ui";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/decisions", label: "AI Council", icon: Scale },
  { href: "/decisions#decisions", label: "Decisions", icon: FileText },
  { href: "/decisions#integrations", label: "Integrations", icon: Puzzle },
  { href: "/decisions#observability", label: "Observability", icon: Activity },
  { href: "/decisions#memory", label: "Memory", icon: Database },
  { href: "/decisions#workflows", label: "Workflows", icon: Workflow },
  { href: "/decisions#policies", label: "Policies", icon: ShieldCheck },
  { href: "/decisions#settings", label: "Settings", icon: Settings },
  { href: "/department", label: "Department", icon: Users },
  { href: "/activity", label: "Activity", icon: Network },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-dvh flex-col bg-background md:h-dvh md:flex-row md:overflow-hidden">
      <aside className="command-rail z-10 flex w-full shrink-0 flex-col border-b border-border bg-surface md:h-full md:w-64 md:border-b-0 md:border-r">
        <div className="flex min-h-14 items-center gap-2.5 border-b border-border px-4 sm:px-5">
          <div className="grid h-8 w-8 place-items-center rounded-md bg-accent text-accent-foreground">
            <Bot className="h-4 w-4" strokeWidth={2.4} />
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[16px] font-semibold">Atlas Finance OS</div>
          </div>
          <StatusDot tone="positive" label="Finance OS online" className="hidden sm:inline-block" />
        </div>

        <nav className="flex gap-1 overflow-x-auto px-3 py-2 md:flex-1 md:flex-col md:gap-1 md:overflow-visible md:py-4">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cx(
                  "flex h-11 min-w-max items-center gap-2.5 rounded-lg px-3 text-[13px] font-medium transition-colors md:min-w-0",
                  active
                    ? "bg-info-bg text-info"
                    : "text-muted-foreground hover:bg-surface-muted hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                <span className="truncate">{item.label}</span>
                {active && <StatusDot tone="info" className="ml-auto hidden md:inline-block" />}
              </Link>
            );
          })}
        </nav>

        <div className="hidden border-t border-border p-3 md:block">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
            <div className="grid h-9 w-9 place-items-center rounded-md bg-foreground/90 text-[11px] font-semibold text-background">
              NR
            </div>
            <div className="min-w-0 flex-1 leading-tight">
              <div className="truncate text-[12px] font-medium">Northwind Robotics</div>
              <div className="truncate text-[10px] text-subtle-foreground">Workspace</div>
            </div>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto bg-background md:h-full">{children}</main>
    </div>
  );
}
