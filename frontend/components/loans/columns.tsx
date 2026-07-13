// U6 — column definitions for the loan-portfolio DenseTable (wireframe: Borrower /
// Employer / Servicer / Balance / Benefit / Monthly / Next / Excep. / Loan). Money is
// integer cents formatted at the render boundary (mono, tabular — the numeric columns
// carry font-mono tabular-nums from the DenseTable). Dates derive from SYSTEM_TIMEZONE,
// never UTC (specs/README).
//
// a11y: the Borrower cell holds the row's REAL <a> (next/link) to the account. The
// DenseTable makes the whole row mouse-clickable but deliberately keeps native row/cell
// semantics (no role/tabIndex), so keyboard + assistive-tech users reach the account
// through this in-cell link. stopPropagation avoids a redundant second navigation from
// the row's onClick.

import Link from "next/link";
import type { Column } from "@/components/Table";
import { StatusPill } from "@/components/Pill";
import { ExceptionCell } from "@/components/loans/ExceptionCell";
import { formatCents, SYSTEM_TIMEZONE } from "@/lib/readModels";
import type { FirestoreTimestamp, LoanWorkbench, WithId } from "@/lib/readModels";

export type LoanRow = WithId<LoanWorkbench>;

function toDate(ts: FirestoreTimestamp | null | undefined): Date | null {
  if (!ts) return null;
  if (typeof ts === "string") {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const maybe = ts as { toDate?: () => Date };
  if (typeof maybe.toDate === "function") {
    try {
      return maybe.toDate();
    } catch {
      return null;
    }
  }
  return null;
}

const SHORT_DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  month: "short",
  day: "numeric",
});

/** "Aug 1" in SYSTEM_TIMEZONE, or an em dash when there is no next contribution. */
export function formatNextDate(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? SHORT_DATE.format(d) : "—";
}

export const LOAN_COLUMNS: Column<LoanRow>[] = [
  {
    key: "borrower",
    header: "Borrower",
    render: (r) => (
      <Link
        href={`/loans/${r.loanId}`}
        onClick={(e) => e.stopPropagation()}
        className="group/borrower -m-1 block max-w-[16rem] rounded-sm p-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        <span className="block truncate font-medium text-ink group-hover/borrower:text-accent">
          {r.borrowerName}
        </span>
        <span className="block truncate text-xs text-ink-3">
          {r.borrowerEmail || r.employerName}
        </span>
      </Link>
    ),
  },
  { key: "employer", header: "Employer", render: (r) => r.employerName },
  { key: "servicer", header: "Servicer", render: (r) => r.servicerName },
  {
    key: "balance",
    header: "Balance",
    align: "right",
    numeric: true,
    render: (r) => formatCents(r.currentBalanceCents),
  },
  {
    key: "benefit",
    header: "Benefit",
    render: (r) => <StatusPill status={r.benefitStatus} />,
  },
  {
    key: "monthly",
    header: "Monthly",
    align: "right",
    numeric: true,
    render: (r) => formatCents(r.baseMonthlyContributionCents),
  },
  {
    key: "next",
    header: "Next",
    render: (r) => (
      <span className="tabular-nums text-ink-2">{formatNextDate(r.nextContributionDate)}</span>
    ),
  },
  {
    key: "exceptions",
    header: "Excep.",
    align: "right",
    numeric: true,
    render: (r) => <ExceptionCell count={r.openExceptionCount} />,
  },
  {
    key: "loan",
    header: "Loan",
    render: (r) => <StatusPill status={r.loanStatus} />,
  },
];
