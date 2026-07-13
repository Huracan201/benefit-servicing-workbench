"use client";

// U7 (stub) — Loan account detail (specs/15 §15.3). Slice B lands this route so the
// portfolio rows + the in-cell Borrower link resolve to a real page instead of a 404;
// Slice C replaces this placeholder with the full account screen (a
// loanWorkbenches/{loanId} + per-loan servicingEvents subscription and the Django
// command actions). Read-only, app-shell-consistent head + skeletons (never a spinner).

import Link from "next/link";
import Card from "@/components/Card";
import Skeleton from "@/components/Skeleton";

export default function LoanAccountPage({
  params,
}: {
  params: { loanId: string };
}) {
  const { loanId } = params;

  return (
    <div className="space-y-4">
      <header className="min-w-0">
        <Link
          href="/loans"
          className="inline-flex items-center gap-1 text-xs font-medium text-ink-3 transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span aria-hidden="true">←</span> Loan portfolio
        </Link>
        <h1 className="mt-1 font-display text-h1 font-semibold text-ink">Account</h1>
        <p className="mt-0.5 text-sm text-ink-2">
          Loading account <span className="font-mono text-ink">{loanId}</span>…
        </p>
      </header>

      <Card title="Account detail" meta="full detail screen lands in the next slice">
        <div role="status" aria-label="Loading account" className="space-y-3">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-24 w-full" />
        </div>
      </Card>
    </div>
  );
}
