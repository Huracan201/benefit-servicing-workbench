"use client";

// TopBar — the 56px header row of the app shell (specs/15 §15.1). It carries the theme
// toggle and the REAL session identity (signed-in email + custom-claim role + sign-out, or a
// Sign-in link when signed out — from useSession()). Role gating on each screen is driven by
// the real claim role; the demo shows different roles by signing in as the seeded
// ops@ / mgr@ / admin@ personas (backend/seed/users.py).

import Link from "next/link";
import type { User } from "firebase/auth";
import ThemeToggle from "@/components/ThemeToggle";
import { useSession } from "@/lib/session";
import type { Role } from "@/lib/types";

const ROLE_LABELS: Record<Role, string> = {
  OPERATIONS_USER: "Operations User",
  SERVICING_MANAGER: "Servicing Manager",
  ADMINISTRATOR: "Administrator",
};

function roleFullLabel(role: Role | null): string | null {
  return role ? ROLE_LABELS[role] ?? role : null;
}

/** Two-letter avatar initials from the signed-in user's display name (preferred) or email. */
function initialsFor(user: User | null): string {
  const source = (user?.displayName || user?.email || "").trim();
  if (!source) return "?";
  const words = source.split(/[\s@._-]+/).filter(Boolean);
  const first = words[0]?.charAt(0) ?? "";
  const last = words.length > 1 ? (words[words.length - 1]?.charAt(0) ?? "") : "";
  const initials = (first + last).toUpperCase();
  return initials || source.slice(0, 2).toUpperCase();
}

export function TopBar() {
  // The REAL session (Firebase custom-claim role) — screens authorize command affordances
  // off this role, and the server authorizes every write regardless.
  const { user, role: claimsRole, status, signOut } = useSession();
  const roleLabel = roleFullLabel(claimsRole);
  const initials = initialsFor(user);

  return (
    <header className="col-start-1 row-start-1 flex items-center gap-3 border-b border-border bg-surface px-4 md:col-start-2">
      {/* Compact brand mark, mobile only (the full brand block lives in the sidebar,
          which is hidden on narrow viewports). */}
      <span
        aria-hidden="true"
        className="grid h-6 w-6 shrink-0 place-items-center rounded-sm bg-accent text-[13px] font-bold text-accent-ink md:hidden"
      >
        B
      </span>

      <div className="flex-1" aria-hidden="true" />

      <ThemeToggle />

      {/* Real session identity — the signed-in email + custom-claim role + sign-out. */}
      {status === "authed" && user ? (
        <div className="flex items-center gap-2.5">
          <div className="hidden min-w-0 text-right sm:block">
            <div className="truncate text-xs font-semibold text-ink">
              {user.email ?? "Signed in"}
            </div>
            {roleLabel ? (
              <div className="truncate text-[10px] uppercase tracking-[0.06em] text-ink-3">
                {roleLabel}
              </div>
            ) : null}
          </div>
          <span
            aria-hidden="true"
            title={user.email ?? undefined}
            className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-accent/20 text-xs font-bold text-accent"
          >
            {initials}
          </span>
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 text-sm text-ink-2 transition-colors hover:border-accent/[0.4] hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Sign out
          </button>
        </div>
      ) : status === "loading" ? (
        <div
          aria-hidden="true"
          className="h-[30px] w-[30px] shrink-0 animate-pulse rounded-full bg-surface-2 motion-reduce:animate-none"
        />
      ) : (
        <Link
          href="/signin"
          className="rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 text-sm font-semibold text-ink-2 transition-colors hover:border-accent/[0.4] hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Sign in
        </Link>
      )}
    </header>
  );
}

export default TopBar;
