import { applyTheme, resolveTheme, THEME_STORAGE_KEY, type Theme } from "@/lib/theme";

type Listener = () => void;

const listeners = new Set<Listener>();

function notify() {
  listeners.forEach((listener) => listener());
}

export function subscribeTheme(onStoreChange: Listener) {
  listeners.add(onStoreChange);
  if (typeof window !== "undefined") {
    window.addEventListener("storage", onStoreChange);
  }
  return () => {
    listeners.delete(onStoreChange);
    if (typeof window !== "undefined") {
      window.removeEventListener("storage", onStoreChange);
    }
  };
}

export function getThemeSnapshot(): Theme {
  if (typeof window === "undefined") return "light";
  return resolveTheme(window.localStorage.getItem(THEME_STORAGE_KEY));
}

export function getServerThemeSnapshot(): Theme {
  return "light";
}

export function persistTheme(theme: Theme) {
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  applyTheme(theme);
  notify();
}
