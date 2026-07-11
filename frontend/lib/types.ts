// TypeScript mirror of the Firestore data model (specs/04) and read models (specs/05).
// String-literal unions match the enum string values EXACTLY (see the shared brief /
// specs/06). Money is always integer US cents (fields end in `Cents`); currency is
// fixed to "USD" for the MVP. These are read-only view shapes — the frontend never
// mutates protected state directly (specs/02, specs/15 §15.2).

import type { Timestamp } from "firebase/firestore";

/** A Firestore instant. Server writes `Timestamp`; some read paths surface it as an ISO string. */
export type FirestoreTimestamp = Timestamp | string;

// ---------------------------------------------------------------------------
// Enums (exact string values — specs/06, shared brief)
// ---------------------------------------------------------------------------

export const CONTRIBUTION_STATUSES = [
  "SCHEDULED",
  "PROCESSING",
  "POSTED",
  "FAILED",
  "RETRY_PENDING",
  "CANCELED",
] as const;
export type ContributionStatus = (typeof CONTRIBUTION_STATUSES)[number];

export const PAYMENT_ATTEMPT_STATUSES = ["STARTED", "SUCCEEDED", "FAILED"] as const;
export type PaymentAttemptStatus = (typeof PAYMENT_ATTEMPT_STATUSES)[number];

export const BENEFIT_STATUSES = [
  "DRAFT",
  "PENDING",
  "ACTIVATING",
  "ACTIVE",
  "SUSPENDED",
  "COMPLETED",
  "TERMINATED",
] as const;
export type BenefitStatus = (typeof BENEFIT_STATUSES)[number];

export const EMPLOYMENT_STATUSES = ["PENDING", "ACTIVE", "LEAVE", "TERMINATED"] as const;
export type EmploymentStatus = (typeof EMPLOYMENT_STATUSES)[number];

export const LOAN_STATUSES = ["ACTIVE", "PAID_OFF", "DELINQUENT", "CLOSED"] as const;
export type LoanStatus = (typeof LOAN_STATUSES)[number];

export const EMPLOYER_STATUSES = ["ACTIVE", "INACTIVE"] as const;
export type EmployerStatus = (typeof EMPLOYER_STATUSES)[number];

export const EXCEPTION_STATUSES = ["OPEN", "IN_REVIEW", "RESOLVED", "DISMISSED"] as const;
export type ExceptionStatus = (typeof EXCEPTION_STATUSES)[number];

export const EXCEPTION_TYPES = [
  "PAYMENT_FAILED",
  "EMPLOYMENT_VERIFICATION_REQUIRED",
  "LOAN_BALANCE_MISMATCH",
  "BENEFIT_CONFIGURATION_ERROR",
  "SERVICER_SYNC_FAILURE",
  "PAYMENT_STUCK_PROCESSING",
  "TASK_FAILED",
] as const;
export type ExceptionType = (typeof EXCEPTION_TYPES)[number];

export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];

/** Numeric, sortable severity rank (specs/04 §4.10, specs/13). */
export const SEVERITY_RANK: Record<Severity, number> = {
  LOW: 10,
  MEDIUM: 20,
  HIGH: 30,
  CRITICAL: 40,
};

export const PAYMENT_FAILURE_CODES = [
  "SERVICER_UNAVAILABLE",
  "SERVICER_TIMEOUT",
  "INSUFFICIENT_FUNDS",
  "ACCOUNT_FROZEN",
  "INVALID_ACCOUNT",
  "NOT_SUBMITTED",
] as const;
export type PaymentFailureCode = (typeof PAYMENT_FAILURE_CODES)[number];

export const IDEMPOTENCY_STATUSES = ["PENDING", "COMPLETED", "FAILED"] as const;
export type IdempotencyStatus = (typeof IDEMPOTENCY_STATUSES)[number];

export const ROLES = ["OPERATIONS_USER", "SERVICING_MANAGER", "ADMINISTRATOR"] as const;
export type Role = (typeof ROLES)[number];

/** Role hierarchy rank (OPERATIONS_USER < SERVICING_MANAGER < ADMINISTRATOR). */
export const ROLE_RANK: Record<Role, number> = {
  OPERATIONS_USER: 10,
  SERVICING_MANAGER: 20,
  ADMINISTRATOR: 30,
};

/** Canonical event type enum (specs/04 §4.9). */
export type ServicingEventType =
  | "BENEFIT_ACTIVATION_STARTED"
  | "BENEFIT_ACTIVATED"
  | "BENEFIT_SUSPENDED"
  | "BENEFIT_RESUMED"
  | "BENEFIT_TERMINATED"
  | "BENEFIT_COMPLETED"
  | "SCHEDULE_SHIFTED"
  | "PAYMENT_PROCESSING"
  | "PAYMENT_POSTED"
  | "PAYMENT_FAILED"
  | "PAYMENT_RETRY_SCHEDULED"
  | "PAYMENT_CANCELED"
  | "PAYMENT_RECONCILED"
  | "FUTURE_CONTRIBUTIONS_CANCELED"
  | "LOAN_BALANCE_UPDATED"
  | "EMPLOYMENT_STATUS_CHANGED"
  | "EXCEPTION_CREATED"
  | "EXCEPTION_RESOLVED"
  | "EXCEPTION_DISMISSED"
  | "MANUAL_NOTE_ADDED"
  | "USER_ROLE_CHANGED"
  | "EMPLOYER_STATUS_CHANGED";

// ---------------------------------------------------------------------------
// Common document fields (specs/README — every top-level entity doc)
// ---------------------------------------------------------------------------

export interface CommonFields {
  createdAt: FirestoreTimestamp;
  updatedAt: FirestoreTimestamp;
  createdBy: string;
  updatedBy: string;
  /** Monotonic audit counter — NOT optimistic-concurrency "version". */
  revision: number;
  schemaVersion: number;
}

// ---------------------------------------------------------------------------
// Entity documents (specs/04)
// ---------------------------------------------------------------------------

export interface Employer extends CommonFields {
  name: string;
  industry: string;
  status: EmployerStatus;
  programName: string;
  currency: "USD";
  totalCommitmentCents: number;
  activeBorrowerCount: number;
  amountPaidCents: number;
  remainingCommitmentCents: number;
}

export interface Borrower extends CommonFields {
  firstName: string;
  lastName: string;
  displayName: string;
  email: string;
  employerId: string;
  employerName: string;
  employmentStatus: EmploymentStatus;
  employmentStartDate: FirestoreTimestamp;
  employmentEndDate: FirestoreTimestamp | null;
  primaryLoanId: string | null;
  primaryBenefitAgreementId: string | null;
}

export interface Loan extends CommonFields {
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  externalLoanReference: string;
  servicerName: string;
  currency: "USD";
  originalPrincipalCents: number;
  currentBalanceCents: number;
  interestRateBasisPoints: number;
  loanStatus: LoanStatus;
  benefitAgreementId: string;
  benefitStatus: BenefitStatus;
  openExceptionCount: number;
  nextContributionDate: FirestoreTimestamp | null;
  nextContributionAmountCents: number | null;
}

export interface BenefitAgreement extends CommonFields {
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  loanId: string;
  currency: "USD";
  totalCommitmentCents: number;
  baseMonthlyContributionCents: number;
  termMonths: number;
  startDate: FirestoreTimestamp;
  endDate: FirestoreTimestamp;
  amountPaidCents: number;
  remainingCommitmentCents: number;
  status: BenefitStatus;
  acceptingPayments: boolean;
  suspendedReason: "LEAVE" | "MANUAL" | null;
  scheduleGenerated: boolean;
  plannedInstallmentCount: number;
  installmentsGenerated: number;
}

export interface ScheduledContribution extends CommonFields {
  benefitAgreementId: string;
  installmentNumber: number;
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  loanId: string;
  currency: "USD";
  scheduledDate: FirestoreTimestamp;
  periodLabel: string;
  scheduledAmountCents: number;
  status: ContributionStatus;
  attemptCount: number;
  currentAttemptId: string | null;
  currentExceptionId: string | null;
  lastAttemptAt: FirestoreTimestamp | null;
  postedAt: FirestoreTimestamp | null;
  postedAmountCents: number | null;
  failureCode: PaymentFailureCode | null;
  failureReason: string | null;
}

/** Append-only; own lifecycle fields (no common CommonFields — specs/04 §4.12a). */
export interface PaymentAttempt {
  contributionId: string;
  loanId: string;
  attemptNumber: number;
  processorIdempotencyKey: string;
  commandIdempotencyKey: string;
  status: PaymentAttemptStatus;
  reconcileAttempts: number;
  requestedAmountCents: number;
  processorReference: string | null;
  failureCode: PaymentFailureCode | null;
  failureReason: string | null;
  startedAt: FirestoreTimestamp;
  completedAt: FirestoreTimestamp | null;
}

/** Immutable, append-only (specs/04 §4.9). */
export interface ServicingEvent {
  eventType: ServicingEventType;
  entityType: string;
  entityId: string;
  loanId: string | null;
  borrowerId: string | null;
  employerId: string | null;
  benefitAgreementId: string | null;
  actorType: "USER" | "SYSTEM";
  actorId: string;
  actorRole: Role | null;
  actorName: string;
  correlationId: string;
  sequence: number;
  metadata: Record<string, unknown>;
  createdAt: FirestoreTimestamp;
}

export interface OperationalException {
  exceptionType: ExceptionType;
  severity: Severity;
  severityRank: number;
  entityType: string;
  entityId: string;
  loanId: string | null;
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  status: ExceptionStatus;
  assignedTo: string | null;
  occurrenceCount: number;
  firstSeenAt: FirestoreTimestamp;
  lastSeenAt: FirestoreTimestamp;
  summary: string;
  details: string;
  resolution: { resolvedBy: string; note: string; resolvedByEvent: string } | null;
  createdAt: FirestoreTimestamp;
  updatedAt: FirestoreTimestamp;
  resolvedAt: FirestoreTimestamp | null;
}

export interface LoanNote {
  body: string;
  authorId: string;
  authorName: string;
  createdAt: FirestoreTimestamp;
}

export interface AppUser extends CommonFields {
  uid: string;
  email: string;
  displayName: string;
  role: Role;
  status: "ACTIVE" | "DISABLED";
}

// ---------------------------------------------------------------------------
// Read models (specs/05) — derived, eventually consistent, never authoritative.
// ---------------------------------------------------------------------------

export interface PortfolioSummaryCurrent {
  activeLoans: number;
  activeBenefitAgreements: number;
  benefitStatusCounts: Partial<Record<BenefitStatus, number>>;
  contributionStatusCounts: Partial<Record<ContributionStatus, number>>;
  openExceptionCount: number;
  openExceptionSeverityCounts: Partial<Record<Severity, number>>;
  openExceptionTypeCounts: Partial<Record<ExceptionType, number>>;
  remainingEmployerCommitmentCents: number;
  updatedAt: FirestoreTimestamp;
}

export interface PortfolioSummaryPeriod {
  periodLabel: string;
  scheduledCents: number;
  postedCents: number;
  failedContributionCount: number;
  updatedAt: FirestoreTimestamp;
}

export interface EmployerSummary {
  employerId: string;
  employerName: string;
  activeBorrowers: number;
  activeBenefits: number;
  monthlyObligationCents: number;
  openExceptionCount: number;
  totalCommitmentCents: number;
  amountPaidCents: number;
  remainingCommitmentCents: number;
  updatedAt: FirestoreTimestamp;
}

export interface EmployerSummaryPeriod {
  periodLabel: string;
  postedCents: number;
  failedCount: number;
  updatedAt: FirestoreTimestamp;
}

/** The widest live mirror — one row per loan for the portfolio table (specs/05 §5.5). */
export interface LoanWorkbench {
  loanId: string;
  borrowerId: string;
  borrowerName: string;
  borrowerEmail: string;
  employerId: string;
  employerName: string;
  employmentStatus: EmploymentStatus;
  servicerName: string;
  currentBalanceCents: number;
  loanStatus: LoanStatus;
  benefitAgreementId: string;
  benefitStatus: BenefitStatus;
  baseMonthlyContributionCents: number;
  nextContributionDate: FirestoreTimestamp | null;
  nextContributionAmountCents: number | null;
  openExceptionCount: number;
  lastActivityAt: FirestoreTimestamp | null;
  lastActivityType: ServicingEventType | null;
  updatedAt: FirestoreTimestamp;
}
