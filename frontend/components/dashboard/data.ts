// Pure derivation helpers for the portfolio dashboard (U5). No hooks, no JSX — just
// shaping the read-model docs (integer cents in, view values out) so the render
// components stay declarative. Every money value stays integer US cents until it hits
// formatCents at the render boundary (specs/README, specs/15 §15.1); percentages are
// derived from cents but never round-tripped through floats in the money path.

import { statusMeta, humanize } from "@/components/statusMeta";
import type { StackedSegment } from "@/components/charts/StackedBar";
import type {
  BenefitStatus,
  ContributionStatus,
  ExceptionType,
  FirestoreTimestamp,
  PortfolioSummaryCurrent,
  PortfolioSummaryPeriod,
  Severity,
} from "@/lib/types";
import { SYSTEM_TIMEZONE, currentPeriodLabel } from "@/lib/readModels";

// ---------------------------------------------------------------------------
// Zero fallbacks — a missing read-model doc renders as zeros, never as a crash
// (specs/15 §15.2: empty state is the zeroed portfolio, not an error).
// ---------------------------------------------------------------------------

export const ZERO_CURRENT: PortfolioSummaryCurrent = {
  activeLoans: 0,
  activeBenefitAgreements: 0,
  benefitStatusCounts: {},
  contributionStatusCounts: {},
  openExceptionCount: 0,
  openExceptionSeverityCounts: {},
  openExceptionTypeCounts: {},
  remainingEmployerCommitmentCents: 0,
  updatedAt: "",
};

export const ZERO_PERIOD: PortfolioSummaryPeriod = {
  periodLabel: currentPeriodLabel(),
  scheduledCents: 0,
  postedCents: 0,
  failedContributionCount: 0,
  updatedAt: "",
};

// ---------------------------------------------------------------------------
// Period-label math (YYYY-MM). Done as integer month arithmetic on the label so it
// never drifts across a UTC/DST boundary (the labels already derive from
// SYSTEM_TIMEZONE via readModels — specs/05 §5.3).
// ---------------------------------------------------------------------------

const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** The last `n` YYYY-MM labels ending at (and including) `current`, oldest first. */
export function lastNPeriodLabels(n: number, current: string = currentPeriodLabel()): string[] {
  const [y, m] = current.split("-").map(Number);
  const base = y * 12 + (m - 1);
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i--) {
    const total = base - i;
    const yy = Math.floor(total / 12);
    const mm = (total % 12) + 1;
    out.push(`${yy}-${String(mm).padStart(2, "0")}`);
  }
  return out;
}

/** "Jul" for a `YYYY-MM` label (chart axis ticks). */
export function shortMonth(label: string): string {
  const m = Number(label.split("-")[1]);
  return MONTHS_SHORT[m - 1] ?? label;
}

/** "July 2026" for a `YYYY-MM` label (the dashboard sub-head). */
export function longMonthYear(label: string): string {
  const [y, m] = label.split("-");
  return `${MONTHS_LONG[Number(m) - 1] ?? m} ${y}`;
}

// ---------------------------------------------------------------------------
// Counts / percentages.
// ---------------------------------------------------------------------------

/** Small integer counts (portfolio tallies) rendered with grouping. */
export function fmtCount(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString("en-US");
}

/** part/whole as a percent, or null when the denominator is 0 (never divide by 0). */
export function ratioPercent(part: number, whole: number): number | null {
  return whole > 0 ? (part / whole) * 100 : null;
}

/** "82.8%" (or "—" when undefined). Display-only — derived from integer cents. */
export function formatPercent(p: number | null, digits = 1): string {
  return p == null ? "—" : `${p.toFixed(digits)}%`;
}

// ---------------------------------------------------------------------------
// Sub-line breakdowns.
// ---------------------------------------------------------------------------

/** "1 critical · 2 high · 2 medium" from the open-exception severity counts. */
export function severityBreakdown(counts: Partial<Record<Severity, number>>): string {
  const order: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
  const parts = order
    .map((s) => ({ n: counts[s] ?? 0, label: statusMeta(s).label.toLowerCase() }))
    .filter((x) => x.n > 0)
    .map((x) => `${x.n} ${x.label}`);
  return parts.length > 0 ? parts.join(" · ") : "none by severity";
}

/** "2 suspended · 1 completed" from the benefit-status counts (activating/suspended/completed). */
export function benefitMixBreakdown(counts: Partial<Record<BenefitStatus, number>>): string {
  const order: BenefitStatus[] = ["ACTIVATING", "SUSPENDED", "COMPLETED"];
  const parts = order
    .map((s) => ({ n: counts[s] ?? 0, label: statusMeta(s).label.toLowerCase() }))
    .filter((x) => x.n > 0)
    .map((x) => `${x.n} ${x.label}`);
  return parts.length > 0 ? parts.join(" · ") : "all active";
}

// ---------------------------------------------------------------------------
// Contribution status mix (the StackedBar). Fixed, meaningful order; each segment's
// token + label come from the shared statusMeta so the chart agrees with every pill.
// ---------------------------------------------------------------------------

const STATUS_MIX_ORDER: ContributionStatus[] = [
  "SCHEDULED",
  "POSTED",
  "CANCELED",
  "FAILED",
  "RETRY_PENDING",
  "PROCESSING",
];

export function statusMixSegments(
  counts: Partial<Record<ContributionStatus, number>>,
): StackedSegment[] {
  return STATUS_MIX_ORDER.map((s) => {
    const meta = statusMeta(s);
    return { key: s, label: meta.label, value: counts[s] ?? 0, token: meta.token };
  });
}

export function statusMixTotal(
  counts: Partial<Record<ContributionStatus, number>>,
): number {
  return STATUS_MIX_ORDER.reduce((sum, s) => sum + (counts[s] ?? 0), 0);
}

// ---------------------------------------------------------------------------
// Open exceptions by type (the horizontal bar rows).
// ---------------------------------------------------------------------------

const EXCEPTION_TYPE_LABELS: Record<ExceptionType, string> = {
  PAYMENT_FAILED: "Payment failed",
  EMPLOYMENT_VERIFICATION_REQUIRED: "Employment verification",
  LOAN_BALANCE_MISMATCH: "Loan balance mismatch",
  BENEFIT_CONFIGURATION_ERROR: "Benefit configuration",
  SERVICER_SYNC_FAILURE: "Servicer sync failure",
  PAYMENT_STUCK_PROCESSING: "Payment stuck processing",
  TASK_FAILED: "Task failed",
};

export interface TypeBarRow {
  key: string;
  label: string;
  value: number;
}

/** Exception-type counts as rows, highest first (ties by label). */
export function exceptionTypeRows(
  counts: Partial<Record<ExceptionType, number>>,
): TypeBarRow[] {
  return (Object.entries(counts ?? {}) as [ExceptionType, number][])
    .map(([t, v]) => ({
      key: t,
      label: EXCEPTION_TYPE_LABELS[t] ?? humanize(t),
      value: v ?? 0,
    }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
}

// ---------------------------------------------------------------------------
// Timestamps for the activity timeline. Formatted in SYSTEM_TIMEZONE so the wall
// clock matches the periods the rest of the app derives (specs/README).
// ---------------------------------------------------------------------------

interface TimestampLike {
  toDate: () => Date;
}
function hasToDate(ts: unknown): ts is TimestampLike {
  return (
    typeof ts === "object" &&
    ts !== null &&
    typeof (ts as TimestampLike).toDate === "function"
  );
}

/** Coerce a Firestore Timestamp | ISO string into a Date (null if unparseable). */
export function toDate(ts: FirestoreTimestamp | null | undefined): Date | null {
  if (ts == null) return null;
  if (hasToDate(ts)) return ts.toDate();
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dayKey(d: Date): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: SYSTEM_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

/** Short event stamp: "09:14" for today, else "Jul 5" — both in SYSTEM_TIMEZONE. */
export function formatEventTimestamp(
  ts: FirestoreTimestamp | null | undefined,
  now: Date = new Date(),
): string {
  const d = toDate(ts);
  if (!d) return "";
  if (dayKey(d) === dayKey(now)) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: SYSTEM_TIMEZONE,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);
  }
  return new Intl.DateTimeFormat("en-US", {
    timeZone: SYSTEM_TIMEZONE,
    month: "short",
    day: "numeric",
  }).format(d);
}

/** Best-effort human detail from a servicing event's free-form metadata (or null). */
export function eventDetail(metadata: Record<string, unknown> | undefined): string | null {
  if (!metadata) return null;
  const candidate = metadata.summary ?? metadata.description ?? metadata.detail;
  return typeof candidate === "string" && candidate.length > 0 ? candidate : null;
}
