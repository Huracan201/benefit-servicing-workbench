"use client";

// U5 — Portfolio dashboard (specs/15 §15.3, U5 wireframe). The showcase read screen:
// portfolio health at a glance. CQRS — this screen only READS Firestore read models
// through the sanctioned subscription hooks (specs/02 P7); it never reads through
// Django and never reads a projection to make a financial DECISION. Aggregates are
// eventually consistent (specs/05 §5.7) — see the consistency note below.
//
// Four subscriptions drive everything:
//   • usePortfolioCurrent()                     — point-in-time totals + status counts
//   • usePortfolioSeries() (ends at the current period) — the 6-month flow trend, whose
//        final doc IS usePortfolioPeriod(currentPeriodLabel()) — this month's KPIs
//   • useEmployerSummaries()                    — per-employer commitment utilization
//   • useRecentServicingEvents(limit)           — the live activity tail
//
// Every money value is integer US cents until formatCents at the render boundary.

import Link from "next/link";
import {
  usePortfolioCurrent,
  useEmployerSummaries,
  useRecentServicingEvents,
  currentPeriodLabel,
} from "@/lib/readModels";
import { usePortfolioSeries } from "@/components/dashboard/usePortfolioSeries";
import { KpiRow } from "@/components/dashboard/KpiRow";
import { FlowChartCard, StatusMixCard } from "@/components/dashboard/Charts";
import { EmployerUtilization } from "@/components/dashboard/EmployerUtilization";
import { RecentActivity } from "@/components/dashboard/RecentActivity";
import { KpiSkeletonGrid, SectionError } from "@/components/dashboard/ui";
import {
  ZERO_CURRENT,
  ZERO_PERIOD,
  fmtCount,
  longMonthYear,
} from "@/components/dashboard/data";

const ACTIVITY_LIMIT = 12;

const ACTION_LINK =
  "inline-flex items-center gap-1.5 rounded-sm border border-border bg-surface-2 px-3 py-1.5 " +
  "text-sm font-semibold text-ink-2 transition-colors hover:text-ink hover:border-accent/[0.4] " +
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export default function DashboardPage() {
  const portfolio = usePortfolioCurrent();
  const series = usePortfolioSeries();
  const employers = useEmployerSummaries();
  const events = useRecentServicingEvents(ACTIVITY_LIMIT);

  // Empty read model → zeros, never a crash (specs/15 §15.2).
  const current = portfolio.data ?? ZERO_CURRENT;
  const period = series.current.data ?? ZERO_PERIOD;
  const scheduledSeries = series.points.map((p) => p.scheduledCents);
  const employerCount = employers.data.length;

  // Hold the tiles as skeletons until both the point-in-time doc and this month's
  // period doc have first resolved, so the money tiles never flash a zero.
  const kpiLoading =
    (portfolio.loading && portfolio.data == null) ||
    (series.current.loading && series.current.data == null);

  return (
    <div className="space-y-4">
      {/* Head */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-h1 font-semibold text-ink">
            Portfolio dashboard
          </h1>
          <p className="mt-0.5 text-sm text-ink-2">
            Servicing health across {fmtCount(employerCount)}{" "}
            {employerCount === 1 ? "employer" : "employers"} ·{" "}
            {longMonthYear(currentPeriodLabel())}
          </p>
        </div>
        <nav aria-label="Quick actions" className="flex shrink-0 items-center gap-2">
          <Link className={ACTION_LINK} href="/payments">
            Open payment queue
          </Link>
          <Link className={ACTION_LINK} href="/exceptions">
            Open exceptions
          </Link>
        </nav>
      </header>

      {/* Consistency note (specs/05 §5.7) — the numbers here are eventually-consistent
          projections; a just-completed action may not have moved a total yet. */}
      <p className="flex items-start gap-2 text-xs text-ink-3">
        <span
          aria-hidden="true"
          className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-info"
        />
        <span>
          Aggregates are eventually consistent and may lag a few seconds behind a
          just-completed action — a posted payment or resolved exception can take a
          moment to move these totals (specs/05 §5.7). Never read them to make a
          servicing decision.
        </span>
      </p>

      {/* KPI tiles */}
      <section aria-label="Portfolio metrics">
        <SectionError error={portfolio.error} context="portfolio metrics" />
        {portfolio.error ? null : kpiLoading ? (
          <KpiSkeletonGrid count={8} />
        ) : (
          <KpiRow
            current={current}
            period={period}
            scheduledSeries={scheduledSeries}
            employerCount={employerCount}
          />
        )}
      </section>

      {/* Charts row — stacks below ~1100px; flow chart gets the wider 1.3fr column
          (U5 wireframe), status mix the 1fr column. */}
      <section
        aria-label="Portfolio charts"
        className="grid grid-cols-1 gap-3 min-[1101px]:grid-cols-[1.3fr_1fr]"
      >
        <FlowChartCard
          points={series.points}
          loading={series.loading}
          error={series.error}
          empty={series.empty}
        />
        <StatusMixCard
          current={current}
          loading={kpiLoading}
          error={portfolio.error}
        />
      </section>

      {/* Employer utilization + recent activity — stacks below ~1100px */}
      <section
        aria-label="Employer utilization and recent activity"
        className="grid grid-cols-1 gap-3 min-[1101px]:grid-cols-2"
      >
        <EmployerUtilization
          employers={employers.data}
          loading={employers.loading}
          error={employers.error}
          empty={employers.empty}
        />
        <RecentActivity
          events={events.data}
          loading={events.loading}
          error={events.error}
          empty={events.empty}
        />
      </section>
    </div>
  );
}
