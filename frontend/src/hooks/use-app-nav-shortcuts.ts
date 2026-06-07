"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isEditableTarget, resolveAppNavFromKeyboard } from "@/lib/app-nav";

/** ⌘⇧1-5 moves across the app sections (Ctrl+⇧ on Windows/Linux). */
export function useAppNavShortcuts() {
  const router = useRouter();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || event.altKey) return;
      if (isEditableTarget(event.target)) return;

      const item = resolveAppNavFromKeyboard(event.key);
      if (!item) return;

      event.preventDefault();
      router.push(item.href);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router]);
}
