"use client";

// SessionBanner — the "session expired, sign in again" surface (specs/12). It renders
// NOTHING while the session is loading, authed, or ordinarily anonymous; only an
// EXPIRED session (a previously-authed token dropped without a deliberate sign-out)
// shows the banner. The outer aria-live region stays mounted so assistive tech
// announces the notice the moment it appears. Dismissible, and re-shows if the session
// expires again after a dismissal.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "@/lib/session";

export function SessionBanner() {
  const { status } = useSession();
  const [dismissed, setDismissed] = useState(false);

  // Reset the dismissal whenever a fresh expiry occurs, so a later expiry re-announces.
  useEffect(() => {
    if (status === "expired") setDismissed(false);
  }, [status]);

  const show = status === "expired" && !dismissed;

  return (
    <div aria-live="polite" className="shrink-0">
      {show ? (
        <div className="flex items-center gap-3 border-b border-warning/[0.35] bg-warning/[0.12] px-4 py-2 text-sm">
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-full bg-warning"
          />
          <p className="min-w-0 flex-1">
            <span className="font-semibold text-warning">Session expired.</span>{" "}
            <span className="text-ink-2">
              Your sign-in is no longer valid — please sign in again to continue.
            </span>
          </p>
          <Link
            href="/signin"
            className="shrink-0 rounded-sm border border-warning/[0.45] px-2.5 py-1 text-sm font-semibold text-warning transition-colors hover:bg-warning/[0.16] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Sign in
          </Link>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            aria-label="Dismiss session-expired notice"
            className="shrink-0 rounded-sm px-1 text-ink-3 transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default SessionBanner;
