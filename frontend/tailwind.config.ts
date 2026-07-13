import type { Config } from "tailwindcss";

// "Ledger + control room" design system (U1 foundation). Colors resolve to CSS
// custom properties defined in app/globals.css (space-separated RGB triplets),
// wrapped as rgb(var(--x) / <alpha-value>) so Tailwind opacity utilities work.
//
// darkMode uses the Tailwind 3.4 `selector` form pointed at [data-theme="dark"],
// so any `dark:` utilities follow the same viewer toggle the CSS vars do.
//
// Canonical token → Tailwind key map (other units MUST use these keys):
//   Chrome:   bg | surface | surface-2 | border | ink | ink-2 | ink-3
//             accent | accent-ink
//   Status:   good | warning | serious | critical | info | neutral
//   Accent (Verdigris) is chrome/interaction ONLY — never a "good" signal.
const config: Config = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Page canvas (utility reads `bg-bg`, `text-bg`, …).
        bg: "rgb(var(--bg) / <alpha-value>)",
        // Card / panel surfaces.
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          2: "rgb(var(--surface-2) / <alpha-value>)",
          // Legacy Phase-1 aliases (kept so the pre-existing scaffold keeps
          // rendering during the Phase-4 transition; prefer the keys above).
          raised: "rgb(var(--surface) / <alpha-value>)",
          muted: "rgb(var(--surface-2) / <alpha-value>)",
        },
        border: "rgb(var(--border) / <alpha-value>)",
        // Text ink ramp.
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          2: "rgb(var(--ink-2) / <alpha-value>)",
          3: "rgb(var(--ink-3) / <alpha-value>)",
        },
        // Single interaction/chrome accent (Verdigris).
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          ink: "rgb(var(--accent-ink) / <alpha-value>)",
          // Legacy alias for the Phase-1 scaffold.
          fg: "rgb(var(--accent-ink) / <alpha-value>)",
        },
        // Status / semantic colors.
        good: "rgb(var(--good) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
        serious: "rgb(var(--serious) / <alpha-value>)",
        critical: "rgb(var(--critical) / <alpha-value>)",
        info: "rgb(var(--info) / <alpha-value>)",
        neutral: "rgb(var(--neutral) / <alpha-value>)",
        // Legacy Phase-1 text aliases (map onto the ink ramp).
        content: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          muted: "rgb(var(--ink-2) / <alpha-value>)",
        },
      },
      borderRadius: {
        DEFAULT: "var(--radius)", // 8px
        sm: "var(--radius-sm)", // 6px
        pill: "var(--radius-pill)", // 999px
      },
      boxShadow: {
        DEFAULT: "var(--shadow)",
        elevation: "var(--shadow)",
      },
      fontFamily: {
        // Public Sans is the default body face.
        sans: [
          "var(--font-public-sans)",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        display: [
          "var(--font-ibm-plex-sans)",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "var(--font-ibm-plex-mono)",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        // Design type scale (px in comments; rem for scaling).
        hero: ["1.8125rem", { lineHeight: "1.15", letterSpacing: "-0.01em" }], // 29 (mono)
        h1: ["1.25rem", { lineHeight: "1.35", letterSpacing: "-0.01em" }], // 20
        h2: ["0.875rem", { lineHeight: "1.4" }], // 14
        body: ["0.8125rem", { lineHeight: "1.5" }], // 13
        sm: ["0.75rem", { lineHeight: "1.4" }], // 12
        xs: ["0.6875rem", { lineHeight: "1.4" }], // 11
        micro: ["0.625rem", { lineHeight: "1.2", letterSpacing: "0.05em" }], // 10 uppercase labels
      },
      fontVariantNumeric: {
        tabular: "tabular-nums",
      },
    },
  },
  plugins: [],
};

export default config;
