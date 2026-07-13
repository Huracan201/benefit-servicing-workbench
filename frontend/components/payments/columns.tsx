// U-C2 — column definitions for the payment operations queue DenseTable (wireframe: Borrower /
// Employer / Scheduled / Amount / Status, plus a per-tab Actions column). Money is integer cents
// formatted at the render boundary (mono, tabular — the numeric columns carry font-mono
// tabular-nums from the DenseTable). Dates derive from SYSTEM_TIMEZONE, never UTC (specs/README);
// see formatScheduledDate in lib/readContributions.
//
// a11y: the Borrower cell holds the row's REAL <a> (next/link) to the account. The DenseTable
// makes the whole row mouse-clickable but keeps native row/cell semantics (no role/tabIndex), so
// keyboard + assistive-tech users reach the account through this in-cell link; stopPropagation
// avoids a redundant second navigation from the row's onClick.
//
// The Actions column is appended by the page ONLY for actionable status tabs (see actionFor); its
// cell delegates to PaymentRowActions, which owns the write-path affordance for the row.

import Link from "next/link";
import type { Column } from "@/components/Table";
import { StatusPill } from "@/components/Pill";
import { PaymentRowActions } from "@/components/payments/PaymentRowActions";
import { formatCents } from "@/lib/readModels";
import {
  formatScheduledDate,
  type ContributionRow,
  type PaymentAction,
} from "@/lib/readContributions";
import type { ContributionStatus } from "@/lib/types";

/**
 * The command a contribution status offers from the queue, or null when none.
 *
 * Wired to the endpoints' ACTUAL source-state preconditions, NOT the user's mental model — the
 * `/process` endpoint accepts {SCHEDULED, RETRY_PENDING} → PROCESSING, while `/retry` accepts
 * FAILED ONLY → RETRY_PENDING (specs/09 §9.1–9.2; specs/openapi.yaml; backend/payments/service.py
 * process_contribution / retry_contribution). So a RETRY_PENDING contribution is advanced with
 * PROCESS, not retry — calling `/retry` on it would 409 INVALID_TRANSITION. PROCESSING is in-flight
 * and POSTED / CANCELED are terminal, so none of those offer an action.
 */
export function actionFor(status: ContributionStatus): PaymentAction | null {
  switch (status) {
    case "SCHEDULED":
    case "RETRY_PENDING":
      return "processContribution";
    case "FAILED":
      return "retryContribution";
    default:
      return null;
  }
}

/** The Borrower / Employer / Scheduled / Amount / Status columns, shared by every status tab. */
export const BASE_PAYMENT_COLUMNS: Column<ContributionRow>[] = [
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
          Installment {r.installmentNumber}
        </span>
      </Link>
    ),
  },
  { key: "employer", header: "Employer", render: (r) => r.employerName },
  {
    key: "scheduledDate",
    header: "Scheduled",
    render: (r) => (
      <span className="tabular-nums text-ink-2">{formatScheduledDate(r.scheduledDate)}</span>
    ),
  },
  {
    key: "amount",
    header: "Amount",
    align: "right",
    numeric: true,
    render: (r) => formatCents(r.scheduledAmountCents),
  },
  {
    key: "status",
    header: "Status",
    render: (r) => <StatusPill status={r.status} />,
  },
];

/** The trailing Actions column for an actionable tab; wires each row to its process / retry command. */
export function actionsColumn(action: PaymentAction): Column<ContributionRow> {
  return {
    key: "actions",
    header: "Actions",
    align: "right",
    render: (r) => <PaymentRowActions contribution={r} action={action} />,
  };
}
