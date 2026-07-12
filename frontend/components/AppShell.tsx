// AppShell — the persistent desktop-first operations chrome (specs/15 §15.1, U1
// wireframe). A CSS grid lays out four regions: a fixed 216px sidebar column and a
// fluid main column, split by a 56px header row —
//
//     ┌───────────┬────────────────────────┐  56px   brand │ top bar
//     │  brand    │        top bar         │
//     ├───────────┼────────────────────────┤  1fr    nav   │ main (scrolls)
//     │  nav      │        main            │
//     └───────────┴────────────────────────┘
//            216px          fluid
//
// The chrome (brand / nav / top bar) is fixed; only <main> scrolls. Below `md` the
// sidebar collapses and the top bar spans full width (with a compact brand mark).
// AppShell itself is presentational and stays a server component — the interactive
// pieces (TopBar's search + "View as" switcher, Nav's active-route state) are their
// own client components.

import type { ReactNode } from "react";
import Nav from "@/components/Nav";
import TopBar from "@/components/TopBar";

export interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="grid h-screen grid-rows-[56px_1fr] overflow-hidden bg-bg text-ink md:grid-cols-[216px_1fr]">
      {/* Brand block — sidebar header (desktop only). */}
      <div className="col-start-1 row-start-1 hidden items-center gap-2.5 border-b border-r border-border bg-surface px-4 md:flex">
        <span
          aria-hidden="true"
          className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-[7px] bg-accent text-[14px] font-bold text-accent-ink"
        >
          B
        </span>
        <span className="min-w-0 leading-none">
          <span className="block truncate text-[13.5px] font-semibold -tracking-[0.01em] text-ink">
            BenefitServicing
          </span>
          <span className="mt-px block text-[10px] uppercase tracking-[0.08em] text-ink-3">
            Workbench
          </span>
        </span>
      </div>

      {/* Header row — global search, "View as" switcher, theme toggle, avatar. */}
      <TopBar />

      {/* Left navigation — the "Servicing" group. */}
      <Nav />

      {/* Scrollable content region. */}
      <main className="col-start-1 row-start-2 min-w-0 overflow-y-auto px-6 py-5 md:col-start-2">
        {children}
      </main>
    </div>
  );
}

export default AppShell;
