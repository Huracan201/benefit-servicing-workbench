// Single source of truth mapping domain values -> a reserved color token + label.
// Pills, severity cells, timeline dots, and charts all read from here so the whole
// UI stays consistent (specs/15 §15.1: color communicates status but NEVER alone —
// every consumer also renders the text label). The token set matches the U1 design
// tokens in tailwind.config.ts / globals.css. The Verdigris `accent` is chrome /
// interaction only and is never used to signal "good".
//
// IMPORTANT (Tailwind JIT): color classes must appear as complete literal strings so
// the content scanner emits them. Never build them by interpolation at a call site —
// go through the PILL_CLASSES / SOLID_BG / INK maps below, whose values are literals.

export type ColorToken =
  | "good"
  | "warning"
  | "serious"
  | "critical"
  | "info"
  | "neutral"
  | "accent";

export interface StatusMeta {
  /** Reserved semantic color token. */
  token: ColorToken;
  /** Human label (never rely on color alone). */
  label: string;
}

// Pill treatment: 12% tint + 26% inset ring + token text color (dot uses currentColor).
export const PILL_CLASSES: Record<ColorToken, string> = {
  good: "text-good bg-good/[0.12] ring-good/[0.26]",
  warning: "text-warning bg-warning/[0.12] ring-warning/[0.26]",
  serious: "text-serious bg-serious/[0.12] ring-serious/[0.26]",
  critical: "text-critical bg-critical/[0.12] ring-critical/[0.26]",
  info: "text-info bg-info/[0.12] ring-info/[0.26]",
  neutral: "text-neutral bg-neutral/[0.12] ring-neutral/[0.26]",
  accent: "text-accent bg-accent/[0.12] ring-accent/[0.26]",
};

// Solid token background (severity rails, timeline dots, meter fills, legend swatches).
export const SOLID_BG: Record<ColorToken, string> = {
  good: "bg-good",
  warning: "bg-warning",
  serious: "bg-serious",
  critical: "bg-critical",
  info: "bg-info",
  neutral: "bg-neutral",
  accent: "bg-accent",
};

// Token text/ink color (SVG marks read this via `currentColor`, hero numbers, etc.).
export const INK: Record<ColorToken, string> = {
  good: "text-good",
  warning: "text-warning",
  serious: "text-serious",
  critical: "text-critical",
  info: "text-info",
  neutral: "text-neutral",
  accent: "text-accent",
};

export function pillClasses(token: ColorToken): string {
  return PILL_CLASSES[token];
}
export function solidBg(token: ColorToken): string {
  return SOLID_BG[token];
}
export function inkColor(token: ColorToken): string {
  return INK[token];
}

/** Title-case an ENUM_VALUE as a fallback label. */
export function humanize(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((w) => (w.length > 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

// -----------------------------------------------------------------------------
// Status -> {token,label}. Keys span every status enum a pill is likely to render
// (specs/06). Overlapping keys across enums agree on token/label; unknown -> neutral.
// -----------------------------------------------------------------------------
const STATUS_META: Record<string, StatusMeta> = {
  // ContributionStatus
  SCHEDULED: { token: "info", label: "Scheduled" },
  PROCESSING: { token: "warning", label: "Processing" },
  POSTED: { token: "good", label: "Posted" },
  FAILED: { token: "critical", label: "Failed" },
  RETRY_PENDING: { token: "serious", label: "Retry pending" },
  CANCELED: { token: "neutral", label: "Canceled" },
  // PaymentAttemptStatus
  STARTED: { token: "warning", label: "Started" },
  SUCCEEDED: { token: "good", label: "Succeeded" },
  // BenefitStatus
  DRAFT: { token: "neutral", label: "Draft" },
  PENDING: { token: "neutral", label: "Pending" },
  ACTIVATING: { token: "warning", label: "Activating" },
  ACTIVE: { token: "good", label: "Active" },
  SUSPENDED: { token: "warning", label: "Suspended" },
  COMPLETED: { token: "good", label: "Completed" },
  TERMINATED: { token: "neutral", label: "Terminated" },
  // EmploymentStatus (ACTIVE/PENDING/TERMINATED shared above)
  LEAVE: { token: "warning", label: "Leave" },
  // LoanStatus
  PAID_OFF: { token: "good", label: "Paid off" },
  DELINQUENT: { token: "critical", label: "Delinquent" },
  CLOSED: { token: "neutral", label: "Closed" },
  // EmployerStatus
  INACTIVE: { token: "neutral", label: "Inactive" },
  // ExceptionStatus
  OPEN: { token: "warning", label: "Open" },
  IN_REVIEW: { token: "info", label: "In review" },
  RESOLVED: { token: "good", label: "Resolved" },
  DISMISSED: { token: "neutral", label: "Dismissed" },
  // Severity (also see severityMeta for rank-driven lookups)
  LOW: { token: "neutral", label: "Low" },
  MEDIUM: { token: "warning", label: "Medium" },
  HIGH: { token: "serious", label: "High" },
  CRITICAL: { token: "critical", label: "Critical" },
};

/** Map a status enum value to its reserved token + label; unknown -> neutral. */
export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status] ?? { token: "neutral", label: humanize(status) };
}

// -----------------------------------------------------------------------------
// ServicingEventType -> {token,label} for the activity timeline (specs/04 §4.9).
// -----------------------------------------------------------------------------
const EVENT_META: Record<string, StatusMeta> = {
  BENEFIT_ACTIVATION_STARTED: { token: "info", label: "Activation started" },
  BENEFIT_ACTIVATED: { token: "good", label: "Benefit activated" },
  BENEFIT_SUSPENDED: { token: "neutral", label: "Benefit suspended" },
  BENEFIT_RESUMED: { token: "good", label: "Benefit resumed" },
  BENEFIT_TERMINATED: { token: "neutral", label: "Benefit terminated" },
  BENEFIT_COMPLETED: { token: "good", label: "Benefit completed" },
  SCHEDULE_SHIFTED: { token: "info", label: "Schedule shifted" },
  PAYMENT_PROCESSING: { token: "warning", label: "Payment processing" },
  PAYMENT_POSTED: { token: "good", label: "Payment posted" },
  PAYMENT_FAILED: { token: "critical", label: "Payment failed" },
  PAYMENT_RETRY_SCHEDULED: { token: "serious", label: "Retry scheduled" },
  PAYMENT_CANCELED: { token: "neutral", label: "Payment canceled" },
  PAYMENT_RECONCILED: { token: "info", label: "Payment reconciled" },
  FUTURE_CONTRIBUTIONS_CANCELED: {
    token: "neutral",
    label: "Future contributions canceled",
  },
  LOAN_BALANCE_UPDATED: { token: "info", label: "Loan balance updated" },
  EMPLOYMENT_STATUS_CHANGED: { token: "info", label: "Employment status changed" },
  EXCEPTION_CREATED: { token: "warning", label: "Exception created" },
  EXCEPTION_RESOLVED: { token: "good", label: "Exception resolved" },
  EXCEPTION_DISMISSED: { token: "neutral", label: "Exception dismissed" },
  MANUAL_NOTE_ADDED: { token: "accent", label: "Note added" },
  USER_ROLE_CHANGED: { token: "info", label: "Role changed" },
  EMPLOYER_STATUS_CHANGED: { token: "info", label: "Employer status changed" },
};

/** Map a servicing event type to its reserved token + label; unknown -> neutral. */
export function eventTypeMeta(eventType: string): StatusMeta {
  return EVENT_META[eventType] ?? { token: "neutral", label: humanize(eventType) };
}

// -----------------------------------------------------------------------------
// Numeric severityRank -> {token,label} (specs/04 §4.10 — rank sorts by importance,
// the `severity` string does not). Thresholds keep unknown ranks meaningful.
// -----------------------------------------------------------------------------
export function severityMeta(severityRank: number): StatusMeta {
  if (severityRank >= 40) return { token: "critical", label: "Critical" };
  if (severityRank >= 30) return { token: "serious", label: "High" };
  if (severityRank >= 20) return { token: "warning", label: "Medium" };
  return { token: "neutral", label: "Low" };
}
