"use client";

// Region 3 — BenefitAgreementCard. The agreement facts (FactsGrid) plus the lifecycle
// ActionCluster: Suspend / Resume / Terminate (danger) / Change employment status.
//
// Every action runs through a `useCommand` handle → the typed command client (CQRS,
// specs/02 P1); the frontend never writes Firestore directly. Optimistic concurrency
// (specs/08): the three benefit-lifecycle actions send the AGREEMENT's `revision` as
// If-Match; the employment change targets the BORROWER, so it sends the borrower's
// revision (its endpoint is /borrowers/{id}/employment-status). Affordances are
// role-gated for UX only — the server still authorizes and a 403 is surfaced by the
// handle's toast. After a command settles, the screen's live SOURCE subscriptions
// reflect the landed state on their own (specs/05 §5.7).

import CommandButton from "@/components/CommandButton";
import CommandFormDialog, {
  type FieldSpec,
} from "@/components/CommandFormDialog";
import Card from "@/components/Card";
import FactsGrid, { type Fact } from "@/components/FactsGrid";
import StatusPill from "@/components/Pill";
import Skeleton from "@/components/Skeleton";
import { useCommand } from "@/hooks/useCommand";
import { formatCents } from "@/lib/format";
import type { CommandBenefitAgreement } from "@/lib/commandTypes";
import type { Role } from "@/lib/types";
import { formatDate } from "@/components/loans/detail/time";

export interface BenefitAgreementCardProps {
  agreement: CommandBenefitAgreement | null;
  borrowerId: string;
  borrowerRevision: number | undefined;
  role: Role | null;
  /** The agreement subscription is still resolving — show a skeleton rather than flashing
   *  the "no benefit agreement" empty state before data arrives. */
  loading?: boolean;
}

// Terminal benefit states cannot be suspended/resumed/terminated further (specs/06).
const TERMINAL = new Set(["TERMINATED", "COMPLETED"]);

const REASON_FIELDS: FieldSpec[] = [
  {
    name: "reason",
    label: "Reason (optional)",
    type: "textarea",
    placeholder: "Add context for the audit trail",
  },
];

const EMPLOYMENT_FIELDS: FieldSpec[] = [
  {
    name: "status",
    label: "New employment status",
    type: "select",
    required: true,
    placeholder: "Select status…",
    options: [
      { value: "ACTIVE", label: "Active" },
      { value: "LEAVE", label: "Leave" },
      { value: "TERMINATED", label: "Terminated" },
    ],
  },
  {
    name: "effectiveDate",
    label: "Effective date",
    type: "text",
    required: true,
    placeholder: "YYYY-MM-DD",
  },
  {
    name: "reason",
    label: "Reason (optional)",
    type: "textarea",
    placeholder: "Add context for the audit trail",
  },
];

export function BenefitAgreementCard({
  agreement,
  borrowerId,
  borrowerRevision,
  role,
  loading = false,
}: BenefitAgreementCardProps) {
  // Hooks are called unconditionally (stable order) even when an action is not shown.
  const suspend = useCommand({
    action: "suspendBenefit",
    id: agreement?.id ?? "",
    role,
    expectedRevision: agreement?.revision,
  });
  const resume = useCommand({
    action: "resumeBenefit",
    id: agreement?.id ?? "",
    role,
    expectedRevision: agreement?.revision,
  });
  const terminate = useCommand({
    action: "terminateBenefit",
    id: agreement?.id ?? "",
    role,
    expectedRevision: agreement?.revision,
  });
  const employment = useCommand({
    action: "changeEmploymentStatus",
    id: borrowerId,
    role,
    expectedRevision: borrowerRevision,
  });

  if (loading) {
    return (
      <Card title="Benefit agreement">
        <span role="status" className="sr-only">
          Loading benefit agreement
        </span>
        <div aria-hidden="true" className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-4 w-28" />
              </div>
            ))}
          </div>
          <div className="flex gap-2 border-t border-border/60 pt-3">
            <Skeleton className="h-7 w-24" />
            <Skeleton className="h-7 w-24" />
          </div>
        </div>
      </Card>
    );
  }

  if (!agreement) {
    return (
      <Card title="Benefit agreement">
        <p className="py-6 text-center text-sm text-ink-3">
          This loan has no benefit agreement.
        </p>
      </Card>
    );
  }

  const status = agreement.status;
  const canSuspend = status === "ACTIVE";
  const canResume = status === "SUSPENDED";
  const canTerminate = !TERMINAL.has(status);
  const scheduleFact = agreement.scheduleGenerated
    ? `${agreement.installmentsGenerated} / ${agreement.plannedInstallmentCount} generated`
    : "Not yet generated";

  const facts: Fact[] = [
    { label: "Agreement", value: agreement.id, mono: true },
    {
      label: "Term",
      value: `${agreement.termMonths} months · ${formatDate(agreement.startDate)} – ${formatDate(agreement.endDate)}`,
    },
    {
      label: "Total commitment",
      value: formatCents(agreement.totalCommitmentCents),
      mono: true,
    },
    {
      label: "Base monthly",
      value: formatCents(agreement.baseMonthlyContributionCents),
      mono: true,
    },
    {
      label: "Accepting payments",
      value: agreement.acceptingPayments ? "Yes" : "No",
    },
    { label: "Schedule", value: scheduleFact },
  ];

  return (
    <Card
      title="Benefit agreement"
      actions={<StatusPill status={status} />}
    >
      <FactsGrid facts={facts} />

      <div className="mt-4 flex flex-wrap gap-2 border-t border-border/60 pt-3">
        {canSuspend ? (
          <>
            <CommandButton handle={suspend} size="sm" />
            <CommandFormDialog
              handle={suspend}
              fields={REASON_FIELDS}
              title="Suspend benefit"
              submitLabel="Suspend benefit"
            />
          </>
        ) : null}

        {canResume ? (
          <>
            <CommandButton handle={resume} size="sm" />
            <CommandFormDialog
              handle={resume}
              fields={REASON_FIELDS}
              title="Resume benefit"
              submitLabel="Resume benefit"
            />
          </>
        ) : null}

        {canTerminate ? (
          <>
            <CommandButton handle={terminate} size="sm" />
            <CommandFormDialog
              handle={terminate}
              fields={REASON_FIELDS}
              title="Terminate benefit"
              submitLabel="Terminate benefit"
            />
          </>
        ) : null}

        <CommandButton handle={employment} size="sm" />
        <CommandFormDialog
          handle={employment}
          fields={EMPLOYMENT_FIELDS}
          title="Change employment status"
          submitLabel="Apply change"
        />
      </div>
    </Card>
  );
}

export default BenefitAgreementCard;
