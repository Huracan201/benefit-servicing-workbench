"use client";

// ThemeToggle — stamps data-theme on <html>, persists the choice to localStorage,
// and defaults to the OS prefers-color-scheme when unset (matching the CSS token
// precedence in globals.css). Both themes are first-class. To avoid a first-paint
// flash, the app layout (owned elsewhere) should also run a tiny pre-hydration
// script that applies the stored theme before React mounts — this component owns the
// runtime toggle + persistence.

import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const STORAGE_KEY = "bsw-theme";

function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  const v = window.localStorage.getItem(STORAGE_KEY);
  return v === "light" || v === "dark" ? v : null;
}

export function ThemeToggle({ className }: { className?: string }) {
  // Start null so SSR and the first client render agree (avoids hydration mismatch);
  // resolve the real theme in an effect after mount.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const initial = storedTheme() ?? systemTheme();
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  function toggle() {
    const next: Theme = (theme ?? systemTheme()) === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore storage failures (private mode) — the DOM attribute still applies.
    }
  }

  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${isDark ? "light" : "dark"} theme`}
      className={[
        "inline-flex items-center gap-2 rounded-sm border border-border bg-surface-2 px-3 py-1.5 text-sm text-ink-2 transition-colors",
        "hover:border-accent/[0.4] hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span aria-hidden="true">{isDark ? "☀" : "◐"}</span>
      {/* Render a stable label; theme is null until mounted, then reflects state. */}
      <span>{theme == null ? "Theme" : isDark ? "Light" : "Dark"}</span>
    </button>
  );
}

export default ThemeToggle;
