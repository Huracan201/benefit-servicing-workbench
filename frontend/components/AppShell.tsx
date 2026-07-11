// AppShell — desktop-first operations chrome (specs/15 §15.1): a fixed left nav
// (Dashboard / Loans / Payments / Exceptions) and a top bar. Restrained cool-slate
// neutrals with a single teal accent. The shell is presentational only.

import type { ReactNode } from "react";
import Nav from "@/components/Nav";

export interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-screen bg-surface text-content">
      {/* Left navigation */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface-raised md:flex">
        <div className="flex h-14 items-center gap-2 border-b border-border px-4">
          <span className="h-6 w-6 rounded bg-accent" aria-hidden="true" />
          <span className="text-sm font-semibold tracking-tight">
            BenefitServicing
          </span>
        </div>
        <Nav />
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-14 items-center justify-between border-b border-border bg-surface-raised px-4">
          <span className="text-sm font-medium text-content-muted md:hidden">
            BenefitServicing Workbench
          </span>
          <div className="hidden text-sm text-content-muted md:block">
            Operations Workbench
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-surface-muted px-3 py-1 text-xs text-content-muted">
              Demo environment
            </span>
          </div>
        </header>

        <main className="flex-1 overflow-x-auto p-6">{children}</main>
      </div>
    </div>
  );
}

export default AppShell;
