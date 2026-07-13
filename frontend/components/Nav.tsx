"use client";

// Primary navigation (specs/15 §15.1): the "Servicing" group — Dashboard · Loans ·
// Payments · Exceptions. Active route is indicated by the teal accent chrome (tinted
// background + left-border accent) PLUS an aria-current marker, never color alone.
//
// Count badges (Payments / Exceptions queues) render placeholder counts today; a later
// slice feeds real read-model counts via the `counts` prop (see contractNotes) — keyed
// by the nav item `href`.

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

export interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

// 17px stroke icons matching the wireframe chrome.
const ICON_DASHBOARD = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="3" y="3" width="7" height="9" rx="1" />
    <rect x="14" y="3" width="7" height="5" rx="1" />
    <rect x="14" y="12" width="7" height="9" rx="1" />
    <rect x="3" y="16" width="7" height="5" rx="1" />
  </svg>
);
const ICON_LOANS = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M3 7h18M3 12h18M3 17h18" />
  </svg>
);
const ICON_PAYMENTS = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M2 10h20" />
  </svg>
);
const ICON_EXCEPTIONS = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M12 3 2 20h20L12 3Z" />
    <path d="M12 10v5M12 18h.01" />
  </svg>
);

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Dashboard", icon: ICON_DASHBOARD },
  { href: "/loans", label: "Loans", icon: ICON_LOANS },
  { href: "/payments", label: "Payments", icon: ICON_PAYMENTS },
  { href: "/exceptions", label: "Exceptions", icon: ICON_EXCEPTIONS },
];

/** Count badges keyed by nav `href`. Placeholder values today; a later slice passes
 *  real read-model counts (payments-to-process, open exceptions) via `Nav counts=…`. */
export type NavCounts = Partial<Record<string, number>>;

const PLACEHOLDER_COUNTS: NavCounts = {
  "/payments": 7,
  "/exceptions": 5,
};

export interface NavProps {
  /** Override the placeholder badge counts (keyed by item href). */
  counts?: NavCounts;
}

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Nav({ counts = PLACEHOLDER_COUNTS }: NavProps) {
  const pathname = usePathname() ?? "/";
  return (
    // Desktop-first by design: the Workbench is a dense operations console (desktop
    // wireframes, wide data tables), so this sidebar is intentionally hidden below `md`.
    // A mobile navigation path (drawer / bottom nav) is out of scope for Phase 4 part 1
    // and owned by a later responsive pass.
    <nav
      aria-label="Primary"
      className="col-start-1 row-start-2 hidden flex-col border-r border-border bg-surface px-2.5 py-3 md:flex"
    >
      <p className="px-2.5 pb-1 pt-2 text-micro font-semibold uppercase tracking-wider text-ink-3">
        Servicing
      </p>

      <ul className="flex flex-col gap-0.5">
        {NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          const count = counts[item.href];
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={[
                  // Reserve the 2px left-accent slot on every item (border-l-transparent)
                  // so activating one causes no layout shift; the tinted outline is a
                  // separate inset ring to avoid a border-left-color conflict.
                  "group flex items-center gap-2.5 rounded-sm border-l-2 border-l-transparent px-2.5 py-2 text-sm font-medium transition-colors motion-reduce:transition-none",
                  "focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent",
                  active
                    ? "border-l-accent bg-accent/[0.11] text-accent ring-1 ring-inset ring-accent/[0.22]"
                    : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                ].join(" ")}
              >
                <span className={active ? "text-accent" : "text-ink-3 group-hover:text-ink-2"}>
                  {item.icon}
                </span>
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                {typeof count === "number" ? (
                  <span
                    aria-label={`${count} items`}
                    className="ml-auto shrink-0 rounded-pill bg-critical/[0.16] px-1.5 py-px text-[10px] font-bold tabular-nums text-critical"
                  >
                    {count}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>

      <p className="mt-auto border-t border-border px-2.5 pt-2.5 text-[10.5px] leading-relaxed text-ink-3">
        Demo build · seed portfolio
        <br />
        Firestore emulator · 4 employers, 20 borrowers
      </p>
    </nav>
  );
}

export default Nav;
