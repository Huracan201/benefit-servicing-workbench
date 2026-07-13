// KpiRow — the eight portfolio-health StatTiles (specs/15 §15.3, U5 wireframe). Hero
// numbers are mono/tabular; money is formatted from integer cents at this render
// boundary via formatCents. Attention tones (critical failures, warning exceptions)
// color the hero number, and the sub-line always repeats the meaning in words so the
// signal survives grayscale. All values are display-only projections — never read to
// make a financial decision (specs/05 §5.7).

import StatTile from "@/components/StatTile";
import StatusPill from "@/components/Pill";
import Sparkline from "@/components/charts/Sparkline";
import { inkColor } from "@/components/statusMeta";
import { formatCents } from "@/lib/format";
import type {
  PortfolioSummaryCurrent,
  PortfolioSummaryPeriod,
} from "@/lib/types";
import {
  benefitMixBreakdown,
  fmtCount,
  formatPercent,
  ratioPercent,
  severityBreakdown,
} from "@/components/dashboard/data";

export interface KpiRowProps {
  current: PortfolioSummaryCurrent;
  period: PortfolioSummaryPeriod;
  /** 6-month scheduled series (integer cents), oldest → newest, for the sparkline. */
  scheduledSeries: number[];
  /** Employer program count (denominator context for remaining commitment). */
  employerCount: number;
}

export function KpiRow({
  current,
  period,
  scheduledSeries,
  employerCount,
}: KpiRowProps) {
  const cs = current.contributionStatusCounts;
  const activeBenefits = current.benefitStatusCounts.ACTIVE ?? 0;
  const retryPending = cs.RETRY_PENDING ?? 0;
  const postedCount = cs.POSTED ?? 0;
  const processing = cs.PROCESSING ?? 0;
  const postedRatio = ratioPercent(period.postedCents, period.scheduledCents);

  return (
    <div className="grid grid-cols-2 gap-3 min-[1101px]:grid-cols-4">
      {/* 1 — Active loans */}
      <StatTile
        label="Active loans"
        value={fmtCount(current.activeLoans)}
        sub={
          <StatusPill
            status="ACTIVE"
            token="good"
            label={`${fmtCount(activeBenefits)} with active benefit`}
          />
        }
      />

      {/* 2 — Scheduled this month (trend sparkline) */}
      <StatTile
        label="Scheduled this month"
        value={formatCents(period.scheduledCents)}
        chart={
          <Sparkline
            values={scheduledSeries}
            token="info"
            height={26}
            ariaLabel="Scheduled contribution amount, last six months"
          />
        }
      />

      {/* 3 — Posted this month */}
      <StatTile
        label="Posted this month"
        value={formatCents(period.postedCents)}
        sub={
          <>
            <span className={inkColor("good")}>{formatPercent(postedRatio)}</span> of
            scheduled · {fmtCount(postedCount)} contributions
          </>
        }
      />

      {/* 4 — Remaining commitment */}
      <StatTile
        label="Remaining commitment"
        value={formatCents(current.remainingEmployerCommitmentCents)}
        sub={`across ${fmtCount(employerCount)} employer ${
          employerCount === 1 ? "program" : "programs"
        }`}
      />

      {/* 5 — Failed contributions */}
      <StatTile
        label="Failed contributions"
        value={fmtCount(period.failedContributionCount)}
        tone="critical"
        sub={
          retryPending > 0 ? (
            <StatusPill
              status="RETRY_PENDING"
              label={`${fmtCount(retryPending)} retry pending`}
            />
          ) : (
            "none awaiting retry"
          )
        }
      />

      {/* 6 — Open exceptions */}
      <StatTile
        label="Open exceptions"
        value={fmtCount(current.openExceptionCount)}
        tone={current.openExceptionCount > 0 ? "warning" : undefined}
        sub={severityBreakdown(current.openExceptionSeverityCounts)}
      />

      {/* 7 — Processing now */}
      <StatTile
        label="Processing now"
        value={fmtCount(processing)}
        sub="in-flight payment attempts"
      />

      {/* 8 — Active benefit agreements */}
      <StatTile
        label="Active benefit agreements"
        value={fmtCount(current.activeBenefitAgreements)}
        sub={benefitMixBreakdown(current.benefitStatusCounts)}
      />
    </div>
  );
}

export default KpiRow;
