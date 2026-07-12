import type { Metadata } from "next";
import AppShell from "@/components/AppShell";
import { ToastProvider } from "@/components/Toast";
import { fontVariables } from "@/lib/fonts";
import "./globals.css";

export const metadata: Metadata = {
  title: "BenefitServicing Workbench",
  description:
    "Operations platform for employer-sponsored student-loan repayment benefits.",
};

// Pre-hydration theme stamp (runs before first paint) — applies a PERSISTED theme
// choice so a toggled user never sees a light/dark flash on load. Users who never
// toggled get no attribute, so the globals.css `@media (prefers-color-scheme)`
// branch keeps following the OS live. ThemeToggle owns runtime toggling/persistence
// (localStorage key "bsw-theme"); this only reads it. Keep the key in sync with
// components/ThemeToggle.tsx.
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("bsw-theme");if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);}}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // fontVariables wires --font-ibm-plex-sans / --font-public-sans /
  // --font-ibm-plex-mono (see lib/fonts.ts) that tailwind maps to
  // font-display / font-sans / font-mono. `font-sans` makes Public Sans the
  // default; body size + tabular-nums come from globals.css.
  // suppressHydrationWarning: the inline script may set data-theme on <html>
  // before React hydrates, which is expected and must not warn.
  return (
    <html lang="en" className={fontVariables} suppressHydrationWarning>
      <body className="bg-bg text-ink font-sans antialiased">
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* ToastProvider must sit high in the tree so useToast() works in any
            screen/action slice (component kit U3). Toast viewport is fixed-position
            so its placement here does not affect layout. */}
        <ToastProvider>
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
