// Region 1 — AccountHeader (specs/15 §15.3). The borrower name as the screen h1 with a
// subline of status pills sourced from the authoritative SOURCE docs (loan + borrower +
// agreement) — never a projection (specs/05 §5.7). Non-status chrome (employer, loan id)
// renders as neutral meta chips; the status values (loan / benefit / employment) render
// through the reserved statusMeta palette so color never stands alone.

import type { ReactNode } from "react";
import StatusPill from "@/components/Pill";
import type { CommandBenefitAgreement, CommandLoan } from "@/lib/commandTypes";
import type { Borrower } from "@/lib/types";

export interface AccountHeaderProps {
  loan: CommandLoan;
  borrower: Borrower | null;
  agreement: CommandBenefitAgreement | null;
}

function MetaChip({
  label,
  value,
  mono,
}: {
  label: string;
  value: ReactNode;
  /** Render the value in the mono/tabular face — reserved for machine tokens (a loan id),
   *  never prose like an employer name. */
  mono?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-pill px-2.5 py-0.5 text-xs ring-1 ring-inset ring-border">
      <span className="font-semibold uppercase tracking-wide text-ink-3">{label}</span>
      <span className={mono ? "font-mono tabular-nums text-ink-2" : "text-ink-2"}>{value}</span>
    </span>
  );
}

export function AccountHeader({ loan, borrower, agreement }: AccountHeaderProps) {
  const name = borrower?.displayName?.trim() || loan.borrowerId;
  const benefitStatus = agreement?.status ?? loan.benefitStatus;

  return (
    <header className="min-w-0">
      <h1 className="font-display text-h1 font-semibold text-ink">{name}</h1>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {borrower?.employerName ? (
          <MetaChip label="Employer" value={borrower.employerName} />
        ) : null}
        <MetaChip label="Loan" value={loan.id} mono />
        <StatusPill status={loan.loanStatus} />
        {benefitStatus ? (
          <StatusPill status={benefitStatus} label={`Benefit ${labelize(benefitStatus)}`} />
        ) : (
          <StatusPill status="DRAFT" token="neutral" label="No benefit" />
        )}
        {borrower ? (
          <StatusPill
            status={borrower.employmentStatus}
            label={`Employment ${labelize(borrower.employmentStatus)}`}
          />
        ) : null}
      </div>
    </header>
  );
}

/** Title-case an enum value for an inline pill prefix ("ACTIVE" → "active"). */
function labelize(value: string): string {
  return value.toLowerCase().replace(/_/g, " ");
}

export default AccountHeader;
