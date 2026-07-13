"use client";

// TopBar — the 56px header row of the app shell (specs/15 §15.1, U1 wireframe chrome).
// Left: a global search input. Right: a DEV/DEMO "View as" role switcher, the U3
// ThemeToggle, and a user avatar. The search is a non-functional affordance for now
// (global search lands in a later slice — see contractNotes).
//
// The "View as" switcher is explicitly a CLIENT-SIDE demo affordance: it sets which
// role the UI *pretends* to be so RoleGate can show/lighten affordances. It is NOT a
// security boundary — Django authorizes every write and Firestore rules authorize
// reads (specs/12). The selection is persisted to localStorage so a later slice can
// expose it to RoleGate consumers via a shared hook/context (see contractNotes).

import { useEffect, useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";
import { ROLES, type Role } from "@/lib/types";

/** localStorage key the demo "View as" role is persisted under. A later slice should
 *  add a shared `useViewAsRole()` hook/context that reads this and feeds RoleGate. */
export const VIEW_AS_ROLE_STORAGE_KEY = "bsw-view-as-role";

/** Default viewer role before any explicit selection (matches the seed demo persona). */
export const DEFAULT_VIEW_AS_ROLE: Role = "SERVICING_MANAGER";

// Short segment labels for the density-first segmented control.
const ROLE_SEGMENTS: ReadonlyArray<{ role: Role; short: string; full: string }> = [
  { role: "OPERATIONS_USER", short: "Ops", full: "Operations User" },
  { role: "SERVICING_MANAGER", short: "Manager", full: "Servicing Manager" },
  { role: "ADMINISTRATOR", short: "Admin", full: "Administrator" },
];

// Demo persona shown in the avatar (initials) — cosmetic only.
const DEMO_USER_NAME = "Alex Operator";
const DEMO_USER_INITIALS = "AO";

function isRole(value: string | null): value is Role {
  return value != null && (ROLES as readonly string[]).includes(value);
}

function storedRole(): Role | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(VIEW_AS_ROLE_STORAGE_KEY);
    return isRole(v) ? v : null;
  } catch {
    // Storage blocked (private mode): fall back to the default role (matches the write
    // path, which already tolerates storage failures).
    return null;
  }
}

export function TopBar() {
  // Start with the default so SSR and the first client render agree (no hydration
  // mismatch); resolve the persisted choice in an effect after mount, mirroring
  // ThemeToggle's pattern.
  const [viewAsRole, setViewAsRole] = useState<Role>(DEFAULT_VIEW_AS_ROLE);

  useEffect(() => {
    const initial = storedRole();
    if (initial) setViewAsRole(initial);
  }, []);

  function selectRole(role: Role) {
    setViewAsRole(role);
    try {
      window.localStorage.setItem(VIEW_AS_ROLE_STORAGE_KEY, role);
    } catch {
      // ignore storage failures (private mode) — the in-memory state still applies.
    }
  }

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

      {/* Global search (affordance only for now). */}
      <div className="relative min-w-0 flex-1 md:max-w-[420px]">
        <svg
          aria-hidden="true"
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          type="search"
          aria-label="Search borrower, loan reference, or employer"
          placeholder="Search borrower, loan reference, or employer…"
          className="h-8 w-full rounded-sm border border-border bg-surface-2 pl-8 pr-2.5 text-sm text-ink placeholder:text-ink-3 focus-visible:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        />
      </div>

      <div className="flex-1" aria-hidden="true" />

      {/* "View as" demo role switcher (client-side affordance, NOT authorization). */}
      <div className="hidden items-center gap-2 sm:flex">
        <span
          id="view-as-label"
          className="text-xs uppercase tracking-wider text-ink-3"
        >
          View as
        </span>
        <div
          role="radiogroup"
          aria-labelledby="view-as-label"
          className="flex overflow-hidden rounded-sm border border-border"
        >
          {ROLE_SEGMENTS.map(({ role, short, full }, i) => {
            const active = viewAsRole === role;
            return (
              <button
                key={role}
                type="button"
                role="radio"
                aria-checked={active}
                aria-label={`View as ${full}`}
                title={`View as ${full} (demo — server still authorizes)`}
                onClick={() => selectRole(role)}
                className={[
                  "px-2.5 py-1 text-sm font-medium transition-colors motion-reduce:transition-none",
                  i > 0 ? "border-l border-border" : "",
                  active
                    ? "bg-accent/[0.12] text-accent"
                    : "bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink",
                  "focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                {short}
              </button>
            );
          })}
        </div>
      </div>

      <ThemeToggle />

      <span
        title={DEMO_USER_NAME}
        aria-label={`Signed in as ${DEMO_USER_NAME}`}
        className="grid h-[30px] w-[30px] shrink-0 place-items-center rounded-full bg-accent/20 text-xs font-bold text-accent"
      >
        {DEMO_USER_INITIALS}
      </span>
    </header>
  );
}

export default TopBar;
