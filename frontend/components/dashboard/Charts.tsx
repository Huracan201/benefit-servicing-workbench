// The dashboard charts row (U5 wireframe):
//   1. FlowChartCard   — monthly Scheduled vs. Posted, a paired Bar on one $-axis.
//   2. StatusMixCard   — the contribution-status StackedBar (direct labels) + the
//                        open-exceptions-by-type horizontal bar rows.
// Every value derives from integer cents / counts; the status palette is carried with
// direct labels (never color alone — specs/15 §15.1). Presentational: the page owns
// the subscriptions and passes resolved data + loading/error down.

import Card from "@/components/Card";
import Bar from "@/components/charts/Bar";
import StackedBar from "@/components/charts/StackedBar";
import { solidBg } from "@/components/statusMeta";
import { formatCents } from "@/lib/format";
import type { PortfolioSummaryCurrent } from "@/lib/types";
import type { PeriodPoint } from "@/components/dashboard/usePortfolioSeries";
import {
  exceptionTypeRows,
  fmtCount,
  statusMixSegments,
  statusMixTotal,
} from "@/components/dashboard/data";
import { ChartSkeleton, SectionError } from "@/components/dashboard/ui";

// ---------------------------------------------------------------------------
// Monthly scheduled vs. posted.
// ---------------------------------------------------------------------------

export interface FlowChartCardProps {
  points: PeriodPoint[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
}

export function FlowChartCard({ points, loading, error, empty }: FlowChartCardProps) {
  const peak = Math.max(0, ...points.flatMap((p) => [p.scheduledCents, p.postedCents]));
  return (
    <Card title="Monthly scheduled vs. posted" meta="last 6 months">
      <SectionError error={error} context="the monthly flow chart" />
      {error ? null : loading && empty ? (
        <ChartSkeleton height={150} />
      ) : empty ? (
        <p className="py-8 text-center text-sm text-ink-3">No period data yet.</p>
      ) : (
        <Bar
          data={points.map((p) => ({
            label: p.monthLabel,
            a: p.scheduledCents,
            b: p.postedCents,
          }))}
          aLabel="Scheduled"
          bLabel="Posted"
          aToken="info"
          bToken="good"
          height={150}
          note={`peak ${formatCents(peak)}`}
        />
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Contribution status mix + open exceptions by type.
// ---------------------------------------------------------------------------

export interface StatusMixCardProps {
  current: PortfolioSummaryCurrent;
  loading: boolean;
  error: Error | null;
}

export function StatusMixCard({ current, loading, error }: StatusMixCardProps) {
  const segments = statusMixSegments(current.contributionStatusCounts);
  const mixTotal = statusMixTotal(current.contributionStatusCounts);
  const typeRows = exceptionTypeRows(current.openExceptionTypeCounts);
  const maxType = Math.max(1, ...typeRows.map((r) => r.value));

  return (
    <Card
      title="Contribution status mix"
      meta={loading ? undefined : `${fmtCount(mixTotal)} this cycle`}
    >
      <SectionError error={error} context="the status mix" />
      {error ? null : loading ? (
        <ChartSkeleton height={26} />
      ) : (
        <>
          {mixTotal === 0 ? (
            <p className="py-4 text-center text-sm text-ink-3">
              No contributions this cycle.
            </p>
          ) : (
            <StackedBar
              segments={segments}
              ariaLabel="Contribution status mix for the current cycle"
            />
          )}

          <div className="mt-4 border-t border-border pt-3">
            <h3 className="font-display text-h2 font-semibold text-ink">
              Open exceptions by type
            </h3>
            {typeRows.length === 0 ? (
              <p className="py-3 text-sm text-ink-3">No open exceptions.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {typeRows.map((row) => (
                  <li key={row.key} className="flex items-center gap-3 text-sm">
                    <span className="w-40 shrink-0 truncate text-ink-2" title={row.label}>
                      {row.label}
                    </span>
                    <span className="h-2 flex-1 overflow-hidden rounded-pill bg-surface-2">
                      <span
                        className={`block h-full rounded-pill ${solidBg("warning")}`}
                        style={{ width: `${(row.value / maxType) * 100}%` }}
                      />
                    </span>
                    <span className="w-6 shrink-0 text-right font-mono tabular-nums text-ink">
                      {row.value}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
