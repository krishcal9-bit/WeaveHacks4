import type { LucideIcon } from "lucide-react";
import { Activity, Database, Scale, Settings, UsersRound } from "lucide-react";

export type AppNavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Digit key with ⌘⇧ / Ctrl+⇧ */
  shortcut: string;
};

export const APP_NAV: AppNavItem[] = [
  { href: "/dashboard", label: "Data", icon: Database, shortcut: "1" },
  { href: "/decisions", label: "Run", icon: Scale, shortcut: "2" },
  { href: "/activity", label: "Activity", icon: Activity, shortcut: "3" },
  { href: "/department", label: "Department", icon: UsersRound, shortcut: "4" },
  { href: "/settings", label: "Settings", icon: Settings, shortcut: "5" },
];

export function appNavShortcutLabel(shortcut: string): string {
  if (typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform)) {
    return `⌘⇧${shortcut}`;
  }
  return `Ctrl+⇧${shortcut}`;
}

export function resolveAppNavFromKeyboard(key: string): AppNavItem | undefined {
  return APP_NAV.find((item) => item.shortcut === key);
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
}
