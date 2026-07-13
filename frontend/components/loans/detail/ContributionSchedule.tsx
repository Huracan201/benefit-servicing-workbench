"use client";

// Region 4 — ContributionSchedule. The full installment schedule (DenseTable ordered by
// installmentNumber) read from the SOURCE `scheduledContributions` docs — never a
// projection (specs/05 §5.7). Selecting a row drives the AttemptsCard (region 5). Each
// eligible row exposes its command: Process (SCHEDULED / RETRY_PENDING) or Retry (FAILED),
// each dispatched through a per-row `useCommand` handle (CQRS, specs/02 P1) with a confirm
// gate. Money is integer cents formatted at the render boundary (never float math).

import type { ReactNode } from "react";
import Card from "@/components/Card";
import CommandButton from "@/components/CommandButton";
import ConfirmAction from "@/components/ConfirmAction";
import StatusPill from "@/components/Pill";
import { Table, type Column } from "@/components/Table";
import { type ColorToken } from "@/components/statusMeta";
import { useCommand } from "@/hooks/useCommand";
import { formatCents } from "@/lib/format";
import type { CommandContribution } from "@/lib/commandTypes";
import type { CommandActionKey } from "@/lib/commandActions";
import type { Role } from "@/lib/types";
import { formatDay, formatMonthYear } from "@/components/loans/detail/time";

export interface ContributionScheduleProps {
  contributions: CommandContribution[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (contributionId: string) => void;
  role: Role | null;
}

/** Which command (if any) a row's status permits (specs/09, task C1 region 4). */
function actionFor(status: string): CommandActionKey | null {
  // /process accepts {SCHEDULED, RETRY_PENDING}; /retry accepts FAILED only (specs/09
  // §9.1-9.2 + openapi). Routing RETRY_PENDING to /retry would 409 INVALID_TRANSITION —
  // this mirrors the payments-queue mapping.
  if (status === "SCHEDULED" || status === "RETRY_PENDING") return "processContribution";
  if (status === "FAILED") return "retryContribution";
  return null;
}

/** A single row's command trigger. Owns its own `useCommand` handle (hook order stays
 *  stable across snapshot changes because exactly one handle is created per row). */
function ScheduleActionCell({
  contribution,
  role,
}: {
  contribution: CommandContribution;
  role: Role | null;
}) {
  const action = actionFor(contribution.status);
  // Always create a handle (stable hook count); the concrete action is null for
  // terminal/in-flight rows, where no trigger is rendered.
  const handle = useCommand({
    action: action ?? "processContribution",
    id: contribution.id,
    role,
  });

  if (!action) {
    return <span className="text-ink-3">—</span>;
  }

  // stopPropagation so activating the command never also toggles row selection.
  return (
    <span
      className="inline-flex"
      onClick={(e) => e.stopPropagation()}
    >
      <CommandButton handle={handle} size="sm" />
      <ConfirmAction
        handle={handle}
        body={
          action === "processContribution"
            ? `Process installment ${pad(contribution.installmentNumber)} for ${formatCents(contribution.scheduledAmountCents)}?`
            : `Retry installment ${pad(contribution.installmentNumber)} for ${formatCents(contribution.scheduledAmountCents)}?`
        }
      />
    </span>
  );
}

function pad(n: number): string {
  return n.toString().padStart(3, "0");
}

export function ContributionSchedule({
  contributions,
  loading,
  selectedId,
  onSelect,
  role,
}: ContributionScheduleProps) {
  const columns: Column<CommandContribution>[] = [
    {
      key: "installment",
      header: "#",
      width: "3.5rem",
      numeric: true,
      // Real button semantics so row-select is keyboard-operable (Enter/Space) and its
      // pressed state is exposed to assistive tech — the accent rail + bold are decoration.
      render: (c) => {
        const selected = c.id === selectedId;
        return (
          <button
            type="button"
            aria-pressed={selected}
            aria-label={`Select installment ${pad(c.installmentNumber)}`}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(c.id);
            }}
            className={[
              "-mx-1 rounded-sm px-1 transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
              selected ? "font-semibold text-accent" : "text-ink hover:text-accent",
            ].join(" ")}
          >
            {pad(c.installmentNumber)}
          </button>
        );
      },
    },
    {
      key: "period",
      header: "Period",
      render: (c) => formatMonthYear(c.scheduledDate),
    },
    {
      key: "scheduledDate",
      header: "Scheduled",
      numeric: true,
      render: (c) => formatDay(c.scheduledDate),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      numeric: true,
      render: (c) => formatCents(c.scheduledAmountCents),
    },
    {
      key: "status",
      header: "Status",
      render: (c) => <StatusPill status={c.status} />,
    },
    {
      key: "attempts",
      header: "Attempts",
      align: "right",
      numeric: true,
      render: (c) => c.attemptCount,
    },
    {
      key: "action",
      header: "",
      align: "right",
      render: (c) => <ScheduleActionCell contribution={c} role={role} />,
    },
  ];

  const rowStripe = (c: CommandContribution): ColorToken | undefined =>
    c.id === selectedId ? "accent" : undefined;

  const meta: ReactNode = contributions.length
    ? `installments ${pad(contributions[0].installmentNumber)}–${pad(contributions[contributions.length - 1].installmentNumber)}`
    : undefined;

  return (
    <Card title="Contribution schedule" meta={meta} flush>
      <Table<CommandContribution>
        caption="Contribution schedule"
        columns={columns}
        rows={contributions}
        rowKey={(c) => c.id}
        loading={loading}
        skeletonRows={8}
        onRowClick={(c) => onSelect(c.id)}
        rowStripe={rowStripe}
        emptyMessage="No scheduled contributions yet"
      />
    </Card>
  );
}

export default ContributionSchedule;
