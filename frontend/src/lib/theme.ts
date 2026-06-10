export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "atlas-theme";

export function resolveTheme(stored: string | null): Theme {
  if (stored === "dark" || stored === "light") return stored;
  // After-hours ledger is the brand default (must match the pre-paint script
  // in app/layout.tsx): dark unless the operator explicitly chose light.
  return "dark";
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}
