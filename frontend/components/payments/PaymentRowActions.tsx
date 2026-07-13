"use client";

// Per-row command cell for the payment operations queue (U-C2; specs/06 §6.1, specs/08, specs/12).
//
// A single write-path affordance driven by the shared useCommand engine: the CommandButton arms
// the intent (mint + freeze one Idempotency-Key) and — because process/retry both `confirm` —
// opens the paired ConfirmAction, whose Confirm submits through the typed command client. The
// screen never renders the command's return value: the row's live SOURCE subscription reflects the
// landed state (the row leaves this status tab once it transitions — specs/05 §5.7).
//
// Role is UX-only affordance (a locked button when the claim is insufficient); the server still
// authorizes every write and a real 403 is surfaced as a toast by the engine (specs/12, specs/15
// §15.2) — a disabled button is never the security boundary.
//
// The wrapper STOPS click propagation: ConfirmDialog renders inline (position:fixed, not a portal),
// so its DOM nests under this cell — without this guard a click on a button, or inside the dialog,
// would bubble to the DenseTable row's navigation onClick and pull the operator off to the account.

import { CommandButton } from "@/components/CommandButton";
import { ConfirmAction } from "@/components/ConfirmAction";
import { FactsGrid, type Fact } from "@/components/FactsGrid";
import { useCommand } from "@/hooks/useCommand";
import { formatCents } from "@/lib/readModels";
import {
  formatScheduledDate,
  type ContributionRow,
  type PaymentAction,
} from "@/lib/readContributions";
import { useSession } from "@/lib/session";

export interface PaymentRowActionsProps {
  contribution: ContributionRow;
  /** The concrete command for this tab's status (process for SCHEDULED / RETRY_PENDING, retry for FAILED). */
  action: PaymentAction;
}

/** Supporting copy under the confirm heading, per action. */
const CONFIRM_BODY: Record<PaymentAction, string> = {
  processContribution:
    "Submit this installment to the payment processor. Its posted or failed outcome will appear here automatically.",
  retryContribution:
    "Schedule a fresh payment attempt for this installment. Progress will appear here automatically.",
};

export function PaymentRowActions({ contribution, action }: PaymentRowActionsProps) {
  // Affordance role from the Firebase custom claim (specs/12); the server still authorizes.
  const { role } = useSession();
  const handle = useCommand({ action, id: contribution.id, role });

  const facts: Fact[] = [
    { label: "Borrower", value: contribution.borrowerName },
    { label: "Employer", value: contribution.employerName },
    { label: "Installment", value: contribution.installmentNumber, mono: true },
    { label: "Scheduled", value: formatScheduledDate(contribution.scheduledDate), mono: true },
    { label: "Amount", value: formatCents(contribution.scheduledAmountCents), mono: true },
  ];

  return (
    // stopPropagation so button / dialog clicks never reach the row's navigation onClick.
    <div className="flex justify-end" onClick={(e) => e.stopPropagation()}>
      <CommandButton handle={handle} size="sm" />
      <ConfirmAction handle={handle} body={CONFIRM_BODY[action]}>
        <FactsGrid facts={facts} columns={1} />
      </ConfirmAction>
    </div>
  );
}

export default PaymentRowActions;
