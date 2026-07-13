"use client";

// usePortfolioSeries — the 6-month flow trend behind the "Scheduled this month"
// sparkline and the "Monthly scheduled vs. posted" bar chart.
//
// The dashboard's data contract is single-doc period summaries (specs/05 §5.3):
// `portfolioSummaries/{YYYY-MM}`. To draw a trend we subscribe to the last N period
// docs — each a bounded single-document subscription (specs/05 §5.6), the sanctioned
// read path. N is a module constant so the hook count is fixed every render
// (rules-of-hooks): the calls are unconditional and always in the same order.
//
// The final subscription is exactly `usePortfolioPeriod(currentPeriodLabel())` — the
// current period — so `current` below is the authoritative this-month doc the KPI
// tiles read (no second subscription to the same doc).

import { useMemo } from "react";
import {
  usePortfolioPeriod,
  type PortfolioSummaryPeriod,
  type ReadModelDoc,
} from "@/lib/readModels";
import { ZERO_PERIOD, lastNPeriodLabels, shortMonth } from "@/components/dashboard/data";

/** Months of history in the trend (fixed — see the rules-of-hooks note above). */
export const TREND_MONTHS = 6;

export interface PeriodPoint {
  /** YYYY-MM period label. */
  label: string;
  /** Axis tick, e.g. "Jul". */
  monthLabel: string;
  scheduledCents: number;
  postedCents: number;
  failedContributionCount: number;
  /** false when that period's summary doc does not exist yet. */
  hasData: boolean;
}

export interface PortfolioSeries {
  /** Oldest → newest, always length {@link TREND_MONTHS}. */
  points: PeriodPoint[];
  /** The current (newest) period doc state — the this-month KPI source. */
  current: ReadModelDoc<PortfolioSummaryPeriod>;
  /** Any period subscription still resolving. */
  loading: boolean;
  /** The first period subscription error, if any. */
  error: Error | null;
  /** No period in the window has a summary doc (empty portfolio / new tenant). */
  empty: boolean;
}

export function usePortfolioSeries(): PortfolioSeries {
  const labels = useMemo(() => lastNPeriodLabels(TREND_MONTHS), []);

  // Unconditional, fixed-order, fixed-count subscriptions (TREND_MONTHS === 6).
  const p0 = usePortfolioPeriod(labels[0]);
  const p1 = usePortfolioPeriod(labels[1]);
  const p2 = usePortfolioPeriod(labels[2]);
  const p3 = usePortfolioPeriod(labels[3]);
  const p4 = usePortfolioPeriod(labels[4]);
  const p5 = usePortfolioPeriod(labels[5]);
  const states = [p0, p1, p2, p3, p4, p5];

  const points: PeriodPoint[] = labels.map((label, i) => {
    const d = states[i].data;
    return {
      label,
      monthLabel: shortMonth(label),
      scheduledCents: d?.scheduledCents ?? ZERO_PERIOD.scheduledCents,
      postedCents: d?.postedCents ?? ZERO_PERIOD.postedCents,
      failedContributionCount:
        d?.failedContributionCount ?? ZERO_PERIOD.failedContributionCount,
      hasData: d != null,
    };
  });

  return {
    points,
    current: states[states.length - 1],
    loading: states.some((s) => s.loading),
    error: states.find((s) => s.error)?.error ?? null,
    empty: points.every((p) => !p.hasData),
  };
}
