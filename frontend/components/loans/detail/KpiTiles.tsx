// Region 2 — KpiTiles. A StatTile row of the account's load-bearing numbers, all read
// from SOURCE docs (the agreement + the live schedule + the exception set), never a
// projection (specs/05 §5.7). Money is integer cents formatted at the render boundary
// with formatCents (never float math); the paid-percentage is a display-only ratio.

import { useMemo } from "react";
import StatTile from "@/components/StatTile";
import StatusPill from "@/components/Pill";
import { formatCents } from "@/lib/format";
import type {
  CommandBenefitAgreement,
  CommandContribution,
  CommandOperationalException,
} from "@/lib/commandTypes";
import { formatDay, toDate } from "@/components/loans/detail/time";

export interface KpiTilesProps {
  agreement: CommandBenefitAgreement | null;
  contributions: CommandContribution[];
  exceptions: CommandOperationalException[];
}

/** Exceptions still needing attention (specs/06 §6.4 — OPEN or IN_REVIEW). */
const ACTIVE_EXCEPTION = new Set(["OPEN", "IN_REVIEW"]);

export function KpiTiles({ agreement, contributions, exceptions }: KpiTilesProps) {
  const nextScheduled = useMemo(
    () =>
      contributions.find((c) => c.status === "SCHEDULED") ?? null,
    [contributions],
  );
  const remainingInstallments = useMemo(
    () => contributions.filter((c) => c.status === "SCHEDULED").length,
    [contributions],
  );
  const openExceptions = useMemo(
    () => exceptions.filter((e) => ACTIVE_EXCEPTION.has(e.status)).length,
    [exceptions],
  );

  const total = agreement?.totalCommitmentCents ?? null;
  const paid = agreement?.amountPaidCents ?? null;
  const remaining = agreement?.remainingCommitmentCents ?? null;

  const paidPct =
    total != null && paid != null && total > 0
      ? `${Math.round((paid / total) * 1000) / 10}%`
      : null;

  const nextDate = nextScheduled ? formatDay(nextScheduled.scheduledDate) : "—";
  const hasNext = toDate(nextScheduled?.scheduledDate) != null;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <StatTile
        label="Total commitment"
        value={formatCents(total)}
        sub={agreement ? `${agreement.termMonths}-month term` : "No benefit agreement"}
      />
      <StatTile
        label="Amount paid"
        value={formatCents(paid)}
        sub={
          paidPct != null ? `${paidPct} of ${formatCents(total)}` : "—"
        }
      />
      <StatTile
        label="Remaining"
        value={formatCents(remaining)}
        sub={`${remainingInstallments} installment${remainingInstallments === 1 ? "" : "s"} left`}
      />
      <StatTile
        label="Next contribution"
        value={nextDate}
        sub={
          hasNext && nextScheduled
            ? formatCents(nextScheduled.scheduledAmountCents)
            : "None scheduled"
        }
      />
      <StatTile
        label="Open exceptions"
        value={openExceptions}
        tone={openExceptions > 0 ? "critical" : "good"}
        aside={
          openExceptions > 0 ? (
            <StatusPill status="OPEN" label="Needs attention" />
          ) : (
            <StatusPill status="RESOLVED" label="Clear" />
          )
        }
      />
    </div>
  );
}

export default KpiTiles;
