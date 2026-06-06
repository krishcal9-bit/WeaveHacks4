"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, LayoutDashboard, Scale, Users } from "lucide-react";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/decisions", label: "Decisions", icon: Scale },
  { href: "/department", label: "Department", icon: Users },
  { href: "/activity", label: "Activity", icon: Activity },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
        <div className="flex h-14 items-center gap-2.5 border-b border-border px-5">
          <div className="grid h-7 w-7 place-items-center rounded-md bg-accent text-[13px] font-semibold text-accent-foreground">
            A
          </div>
          <div className="leading-tight">
            <div className="text-[13px] font-semibold tracking-tight">Atlas</div>
            <div className="text-[10px] uppercase tracking-[0.12em] text-subtle-foreground">
              Finance OS
            </div>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 px-3 py-4">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] font-medium transition-colors ${
                  active
                    ? "bg-surface-muted text-foreground"
                    : "text-muted-foreground hover:bg-surface-muted hover:text-foreground"
                }`}
              >
                <Icon className="h-4 w-4" strokeWidth={1.75} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
            <div className="grid h-7 w-7 place-items-center rounded-md bg-foreground/90 text-[11px] font-semibold text-background">
              NR
            </div>
            <div className="leading-tight">
              <div className="text-[12px] font-medium">Northwind Robotics</div>
              <div className="text-[10px] text-subtle-foreground">Series A · Austin, TX</div>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto bg-background">{children}</main>
    </div>
  );
}
