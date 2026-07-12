// Typed request/response shapes for the Django command API (openapi.yaml, specs/11).
//
// These mirror the openapi component schemas, NOT the Firestore document shapes in
// `types.ts` — command responses are ISO-string dates and omit the audit `CommonFields`.
// Per specs/02 the UI SHOULD treat a command's response body as *advisory* and resolve
// the authoritative outcome by subscribing to the mutated entity's read model; these
// types exist so a caller that does inspect the body stays type-safe. Enum unions are
// reused from `types.ts` so there is a single source of truth for the string values.

import type {
  BenefitStatus,
  ContributionStatus,
  EmployerStatus,
  EmploymentStatus,
  ExceptionStatus,
  ExceptionType,
  LoanStatus,
  PaymentAttemptStatus,
  PaymentFailureCode,
  Role,
  Severity,
} from "@/lib/types";

/** All command-response timestamps are ISO-8601 strings (openapi `format: date-time`). */
type IsoDateTime = string;

// ---------------------------------------------------------------------------
// Async-operation envelope (openapi `OperationStatus`) — the 202 body
// ---------------------------------------------------------------------------

export type OperationState = "ACCEPTED" | "IN_PROGRESS" | "COMPLETED" | "FAILED";

/** Advisory status returned with a 202; the UI should still watch Firestore. */
export interface OperationStatus {
  state: OperationState;
  operation: string;
  entityType?: string;
  entityId?: string;
  retryAfterSeconds?: number;
  correlationId?: string;
}

// ---------------------------------------------------------------------------
// Resource representations (openapi `schemas` → command results)
// ---------------------------------------------------------------------------

export interface CommandBenefitAgreement {
  id: string;
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  loanId: string;
  currency: "USD";
  totalCommitmentCents: number;
  baseMonthlyContributionCents: number;
  termMonths: number;
  startDate: IsoDateTime;
  endDate: IsoDateTime;
  amountPaidCents: number;
  remainingCommitmentCents: number;
  status: BenefitStatus;
  acceptingPayments: boolean;
  suspendedReason: "LEAVE" | "MANUAL" | null;
  scheduleGenerated: boolean;
  plannedInstallmentCount: number;
  installmentsGenerated: number;
  revision: number;
}

export interface CommandContribution {
  id: string;
  benefitAgreementId: string;
  installmentNumber: number;
  borrowerId: string;
  borrowerName: string;
  employerId: string;
  employerName: string;
  loanId: string;
  currency: "USD";
  scheduledDate: IsoDateTime;
  periodLabel: string;
  scheduledAmountCents: number;
  postedAmountCents: number | null;
  status: ContributionStatus;
  attemptCount: number;
  currentAttemptId: string | null;
  currentExceptionId: string | null;
  lastAttemptAt: IsoDateTime | null;
  postedAt: IsoDateTime | null;
  failureCode: PaymentFailureCode | null;
  failureReason: string | null;
  revision: number;
}

export interface CommandPaymentAttempt {
  id: string;
  contributionId: string;
  loanId: string;
  attemptNumber: number;
  status: PaymentAttemptStatus;
  requestedAmountCents: number;
  processorReference: string | null;
  reconcileAttempts: number;
  failureCode: PaymentFailureCode | null;
  failureReason: string | null;
  startedAt: IsoDateTime;
  completedAt: IsoDateTime | null;
}

export interface CommandOperationalException {
  id: string;
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
  summary: string;
  details: string;
  firstSeenAt: IsoDateTime;
  lastSeenAt: IsoDateTime;
  resolvedAt: IsoDateTime | null;
}

export interface CommandLoan {
  id: string;
  borrowerId: string;
  employerId: string;
  currency: "USD";
  currentBalanceCents: number;
  loanStatus: LoanStatus;
  benefitAgreementId: string | null;
  benefitStatus: BenefitStatus | null;
  revision: number;
}

export interface CommandNote {
  id: string;
  loanId: string;
  text: string;
  authorId: string;
  authorName: string;
  createdAt: IsoDateTime;
}

export interface CommandUser {
  uid: string;
  email: string;
  displayName: string;
  role: Role;
  status: "ACTIVE" | "DISABLED";
  revision: number;
}

// ---------------------------------------------------------------------------
// Composite command results
// ---------------------------------------------------------------------------

export interface ProcessContributionBalances {
  postedAmountCents: number;
  loanCurrentBalanceCents: number;
  benefitAmountPaidCents: number;
  benefitRemainingCommitmentCents: number;
}

/** `processContribution` result — inspect `contribution.status` for POSTED vs FAILED. */
export interface ProcessContributionResult {
  contribution: CommandContribution;
  attempt: CommandPaymentAttempt;
  /** Present when POSTED; null on FAILED. */
  balances: ProcessContributionBalances | null;
  resolvedExceptionId: string | null;
  correlationId: string;
}

export type FutureCancellationState = "NOT_APPLICABLE" | "IN_PROGRESS" | "COMPLETED";

export interface EmploymentChangeResult {
  borrowerId: string;
  employmentStatus: EmploymentStatus;
  employmentEndDate: IsoDateTime | null;
  benefit: CommandBenefitAgreement | null;
  futureCancellation: { state: FutureCancellationState };
  correlationId: string;
}

export interface SetEmployerStatusResult {
  employerId: string;
  status: EmployerStatus;
}

// ---------------------------------------------------------------------------
// Request bodies (openapi request schemas)
// ---------------------------------------------------------------------------

export interface ActivateRequest {
  /** Optional override; defaults to the agreement's configured start date. `YYYY-MM-DD`. */
  startDate?: string;
  /** Optimistic-concurrency guard (also expressible via the If-Match header). */
  expectedRevision?: number;
}

/** Body for suspend / resume / terminate. */
export interface ReasonRequest {
  reason?: string;
  /** `YYYY-MM-DD`. */
  effectiveDate?: string;
  expectedRevision?: number;
}

export interface EmploymentStatusChangeRequest {
  /** PENDING is never a legal target (specs/06 §6.5). */
  status: "ACTIVE" | "LEAVE" | "TERMINATED";
  /** `YYYY-MM-DD`. Required. */
  effectiveDate: string;
  reason?: string;
  expectedRevision?: number;
}

export interface CreateExceptionRequest {
  exceptionType: ExceptionType;
  entityType: string;
  entityId: string;
  summary: string;
  details?: string;
  /** Optional override; defaults from the specs/04 §4.10 type map. */
  severity?: Severity | null;
}

export interface AssignExceptionRequest {
  /**
   * Omit the field entirely to assign to the caller ("assign to me"); pass explicit
   * `null` to unassign; a uid to assign to that user (specs/06 §6.4).
   */
  assignToUid?: string | null;
}

export interface ResolveExceptionRequest {
  /** Non-empty. */
  note: string;
}

export interface DismissExceptionRequest {
  /** Non-empty. */
  reason: string;
  note?: string;
}

export interface AddNoteRequest {
  /** Non-empty; empty is rejected 400. */
  text: string;
}

export interface SetRoleRequest {
  role: Role;
}

export interface SetEmployerStatusRequest {
  status: EmployerStatus;
}
