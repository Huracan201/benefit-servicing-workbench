"use client";

// ThemeToggle — on an explicit user choice it stamps data-theme on <html> and
// persists it to localStorage; with no stored choice it leaves data-theme ABSENT and
// follows the OS prefers-color-scheme live (globals.css tracks it), mirroring OS
// changes into the button label. Both themes are first-class. A tiny pre-hydration
// script in the app layout applies a stored choice before React mounts to avoid a
// first-paint flash — this component owns the runtime toggle + persistence.

import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const STORAGE_KEY = "bsw-theme";

function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function storedTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    // Storage blocked (private mode / disabled cookies): fall back to OS-follow so the
    // mount effect still installs the prefers-color-scheme listener below.
    return null;
  }
}

export function ThemeToggle({ className }: { className?: string }) {
  // Start null so SSR and the first client render agree (avoids hydration mismatch);
  // resolve the real theme in an effect after mount.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const stored = storedTheme();
    if (stored) {
      // Explicit user choice: stamp it (matches the layout pre-hydration script) and
      // do NOT follow the OS.
      setTheme(stored);
      document.documentElement.setAttribute("data-theme", stored);
      return;
    }
    // No explicit choice: leave data-theme ABSENT so globals.css's
    // `@media (prefers-color-scheme)` keeps following the OS live. Track the OS purely
    // to keep the toggle's displayed label/icon in sync as it changes.
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setTheme(mq.matches ? "dark" : "light");
    const onChange = (e: MediaQueryListEvent) => {
      // Once the user makes an explicit choice, stop mirroring the OS.
      if (storedTheme()) return;
      setTheme(e.matches ? "dark" : "light");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
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
