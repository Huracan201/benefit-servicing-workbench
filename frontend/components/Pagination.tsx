"use client";

// Pagination — cursor-based prev/next (specs/05: read models paginate by document
// cursor, not offset). Buttons disable when there is no cursor in that direction.
// A center label carries page/range context so state is not conveyed by button
// affordance alone.

import type { ReactNode } from "react";

export interface PaginationProps {
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  /** Disable both controls (e.g. while a page is loading). */
  loading?: boolean;
  /** Center label, e.g. "Showing 1–25" or "Page 2". */
  label?: ReactNode;
  className?: string;
}

const btn =
  "inline-flex items-center gap-1 rounded-sm border border-border bg-surface-2 px-3 py-1.5 text-sm font-medium text-ink-2 transition-colors hover:text-ink hover:border-accent/[0.4] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:text-ink-2 disabled:hover:border-border";

export function Pagination({
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  loading = false,
  label,
  className,
}: PaginationProps) {
  return (
    <nav
      aria-label="Pagination"
      className={[
        "flex items-center justify-between gap-3",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <button
        type="button"
        className={btn}
        onClick={onPrev}
        disabled={loading || !hasPrev}
        aria-label="Previous page"
      >
        <span aria-hidden="true">←</span> Prev
      </button>
      {label != null ? (
        <span className="text-xs tabular-nums text-ink-3">{label}</span>
      ) : (
        <span aria-hidden="true" />
      )}
      <button
        type="button"
        className={btn}
        onClick={onNext}
        disabled={loading || !hasNext}
        aria-label="Next page"
      >
        Next <span aria-hidden="true">→</span>
      </button>
    </nav>
  );
}

export default Pagination;
