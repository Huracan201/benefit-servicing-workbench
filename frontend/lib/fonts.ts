// Self-hosted webfonts (U1 design foundation). next/font/google downloads and
// self-hosts these at build time, so at runtime there is no request to a Google
// CDN — which keeps the app CSP-safe (specs/12) and offline-friendly. Each font
// is exposed as a CSS variable that tailwind.config.ts maps to a fontFamily:
//   --font-ibm-plex-sans → font-display  (headings / hero)
//   --font-public-sans   → font-sans     (default body)
//   --font-ibm-plex-mono → font-mono     (tabular money + ids)
//
// IBM Plex Sans / IBM Plex Mono ship as static weights on Google Fonts, so an
// explicit `weight` array is required; Public Sans is a variable font, so it
// needs none.
import { IBM_Plex_Mono, IBM_Plex_Sans, Public_Sans } from "next/font/google";

export const displayFont = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-ibm-plex-sans",
});

export const bodyFont = Public_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-public-sans",
});

export const monoFont = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-ibm-plex-mono",
});

// Space-joined class list to spread onto <html>/<body>; wires all three CSS vars.
export const fontVariables = `${displayFont.variable} ${bodyFont.variable} ${monoFont.variable}`;
