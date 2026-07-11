// StatusBadge — the primary status idiom (specs/15 §15.1). Color communicates status
// but NEVER alone: every badge renders a text label (and carries an ARIA label), so it
// survives color-blindness and grayscale. The color set is reserved and consistent
// across every status enum. Tones are tuned for both light and dark themes.

import type { ReactNode } from "react";

export type BadgeTone =
  | "neutral"
  | "info"
  | "progress"
  | "success"
  | "warning"
  | "danger"
  | "critical";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral:
    "bg-slate-100 text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-600",
  info: "bg-sky-100 text-sky-800 ring-sky-300 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-700",
  progress:
    "bg-indigo-100 text-indigo-800 ring-indigo-300 dark:bg-indigo-950 dark:text-indigo-300 dark:ring-indigo-700",
  success:
    "bg-emerald-100 text-emerald-800 ring-emerald-300 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-700",
  warning:
    "bg-amber-100 text-amber-900 ring-amber-300 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-700",
  danger:
    "bg-rose-100 text-rose-800 ring-rose-300 dark:bg-rose-950 dark:text-rose-300 dark:ring-rose-700",
  critical:
    "bg-red-600 text-white ring-red-700 dark:bg-red-700 dark:text-white dark:ring-red-500",
};

// Map every domain status value to a reserved tone. Keys cover the enums a badge is
// likely to render (specs/06); unknown values fall back to `neutral`.
const STATUS_TONES: Record<string, BadgeTone> = {
  // ContributionStatus
  SCHEDULED: "info",
  PROCESSING: "progress",
  POSTED: "success",
  FAILED: "danger",
  RETRY_PENDING: "warning",
  CANCELED: "neutral",
  // PaymentAttemptStatus
  STARTED: "progress",
  SUCCEEDED: "success",
  // BenefitStatus
  DRAFT: "neutral",
  PENDING: "info",
  ACTIVATING: "progress",
  ACTIVE: "success",
  SUSPENDED: "warning",
  COMPLETED: "success",
  TERMINATED: "neutral",
  // EmploymentStatus (ACTIVE/PENDING/TERMINATED shared above)
  LEAVE: "warning",
  // LoanStatus
  PAID_OFF: "success",
  DELINQUENT: "danger",
  CLOSED: "neutral",
  // EmployerStatus
  INACTIVE: "neutral",
  // ExceptionStatus
  OPEN: "danger",
  IN_REVIEW: "progress",
  RESOLVED: "success",
  DISMISSED: "neutral",
  // Severity
  LOW: "neutral",
  MEDIUM: "info",
  HIGH: "warning",
  CRITICAL: "critical",
};

export function toneForStatus(status: string): BadgeTone {
  return STATUS_TONES[status] ?? "neutral";
}

export interface StatusBadgeProps {
  /** The status enum value; also used to pick the reserved tone unless `tone` is set. */
  status: string;
  /** Human label. Defaults to a title-cased version of `status`. */
  label?: ReactNode;
  /** Override the tone; otherwise derived from `status`. */
  tone?: BadgeTone;
}

function humanize(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function StatusBadge({ status, label, tone }: StatusBadgeProps) {
  const resolvedTone = tone ?? toneForStatus(status);
  const text = label ?? humanize(status);
  return (
    <span
      role="status"
      aria-label={typeof text === "string" ? text : status}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[resolvedTone]}`}
    >
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-full bg-current opacity-70"
      />
      {text}
    </span>
  );
}

export default StatusBadge;
