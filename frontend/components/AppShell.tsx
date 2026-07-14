"use client";

// AppShell — the persistent desktop-first operations chrome (specs/15 §15.1, U1
// wireframe) PLUS the client-side auth gate. A signed-out user must never see the
// workbench: its reads are denied by the Firestore rules, which would render a wall of
// permission-denied errors (and leaking the screen layout pre-auth is poor UX anyway).
// So the chrome + page only mount once the session is `authed`; otherwise the user is
// sent to /signin. The sign-in screen itself renders bare (no chrome), centered.
//
// Layout (authed): a CSS grid — a fixed 216px sidebar column and a fluid main column,
// split by a 56px header row; only <main> scrolls. Below `md` the sidebar collapses and
// the top bar spans full width (with a compact brand mark).

import type { ReactNode } from "react";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import Nav from "@/components/Nav";
import SessionBanner from "@/components/SessionBanner";
import TopBar from "@/components/TopBar";
import { useSession } from "@/lib/session";

const SIGN_IN_ROUTE = "/signin";

export interface AppShellProps {
  children: ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { status } = useSession();
  const onSignIn = pathname === SIGN_IN_ROUTE;

  // Redirects run in an effect, never during render. A signed-out user on a protected
  // route -> /signin; an already-signed-in user on /signin -> the dashboard. An
  // `expired` session is intentionally left in place so its SessionBanner can offer to
  // re-authenticate without losing the operator's context.
  useEffect(() => {
    if (!onSignIn && status === "anonymous") {
      router.replace(SIGN_IN_ROUTE);
    } else if (onSignIn && status === "authed") {
      router.replace("/");
    }
  }, [onSignIn, status, router]);

  // The sign-in screen renders bare (no operations chrome), centered.
  if (onSignIn) {
    if (status === "authed") return <ShellFallback />; // redirecting away; avoid a flash
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg px-6">
        {children}
      </div>
    );
  }

  // Protected route: don't mount the workbench (and its rule-denied reads) until the
  // session is known signed-in. `loading` (initial resolve) and `anonymous` (mid-redirect)
  // show a quiet placeholder instead of the dashboard-with-errors.
  if (status !== "authed" && status !== "expired") {
    return <ShellFallback />;
  }

  // authed | expired -> the full chrome (expired additionally surfaces the SessionBanner).
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg text-ink">
      {/* Session-expired banner — renders nothing unless the session has expired. */}
      <SessionBanner />

      <div className="grid min-h-0 flex-1 grid-rows-[56px_1fr] md:grid-cols-[216px_1fr]">
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

        {/* Header row — theme toggle, session identity, sign out. */}
        <TopBar />

        {/* Left navigation — the "Servicing" group. */}
        <Nav />

        {/* Scrollable content region. */}
        <main className="col-start-1 row-start-2 min-w-0 overflow-y-auto px-6 py-5 md:col-start-2">
          {children}
        </main>
      </div>
    </div>
  );
}

// Quiet full-screen placeholder shown while auth resolves or a redirect is in flight —
// deliberately minimal (the brand mark, pulsing) so it never flashes the dashboard or
// its rule-denied read errors.
function ShellFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg">
      <span
        aria-hidden="true"
        className="grid h-8 w-8 animate-pulse place-items-center rounded-[9px] bg-accent text-base font-bold text-accent-ink"
      >
        B
      </span>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export default AppShell;
