export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "atlas-theme";

export function resolveTheme(stored: string | null): Theme {
  if (stored === "dark" || stored === "light") return stored;
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}
