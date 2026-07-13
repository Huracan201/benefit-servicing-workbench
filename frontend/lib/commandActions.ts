// The single registry of every mutating operator action, DERIVED from the authoritative
// contract (specs/openapi.yaml), the role matrix (specs/12 §12.2), and the state machines
// (specs/06). Every write-path affordance in the workbench (buttons, confirm dialogs,
// worklist actions) reads its metadata from here and dispatches through `INVOKERS`, so the
// role gate, confirmation policy, optimistic-concurrency policy, and async-poll behavior
// are defined ONCE rather than re-derived per screen.
//
// Field derivation (all traceable to the contract, not guessed):
//   requires      — the minimum role from the specs/12 §12.2 capability matrix.
//   usesIfMatch   — endpoints that accept `If-Match` (the benefit-agreement/borrower
//                   revision): activate/suspend/resume/terminate + employment-status.
//                   Only suspend/resume/terminate/employment are in this registry.
//   mayReturn202  — endpoints that may enqueue async work and answer 202/OperationInProgress
//                   (openapi), plus the three the spec calls out as cascading async work
//                   (process, terminate, employment-status). When true the caller may get a
//                   `pending` outcome and MUST reflect the landed state from its Firestore
//                   SOURCE subscription, never a projection (specs/05 §5.7).
//   tone/confirm  — danger for irreversible/terminal or privileged actions (terminate,
//                   dismiss, setUserRole); confirm for every state change except the light,
//                   append-only note.

import {
  addLoanNote,
  assignException,
  changeEmploymentStatus,
  createException,
  dismissException,
  markExceptionInReview,
  processContribution,
  resolveException,
  resumeBenefit,
  retryContribution,
  setEmployerStatus,
  setUserRole,
  suspendBenefit,
  terminateBenefit,
} from "@/lib/commandClient";
import type { CommandCallOptions, CommandOutcome } from "@/lib/commandClient";
import type {
  AddNoteRequest,
  AssignExceptionRequest,
  CreateExceptionRequest,
  DismissExceptionRequest,
  EmploymentStatusChangeRequest,
  ReasonRequest,
  ResolveExceptionRequest,
  SetEmployerStatusRequest,
  SetRoleRequest,
} from "@/lib/commandTypes";
import type { Role } from "@/lib/types";

// ---------------------------------------------------------------------------
// Action keys + metadata
// ---------------------------------------------------------------------------

/** Every mutating action the operator UI can invoke (one per relevant command endpoint). */
export type CommandActionKey =
  | "processContribution"
  | "retryContribution"
  | "suspendBenefit"
  | "resumeBenefit"
  | "terminateBenefit"
  | "changeEmploymentStatus"
  | "addLoanNote"
  | "createException"
  | "assignException"
  | "markExceptionInReview"
  | "resolveException"
  | "dismissException"
  | "setEmployerStatus"
  | "setUserRole";

export interface CommandActionMeta {
  /** The action's own key (so a meta value is self-describing when passed around). */
  key: CommandActionKey;
  /** Imperative button/menu label, e.g. "Suspend benefit". */
  label: string;
  /** Minimum role for the affordance (UX gate only; the server still authorizes). */
  requires: Role;
  /** Whether the UI must confirm before submitting (all state changes except a note). */
  confirm: boolean;
  /** `danger` for irreversible/terminal or privileged actions; otherwise `primary`. */
  tone: "primary" | "danger";
  /** Carries the caller's `expectedRevision` as `If-Match` for stale-write protection. */
  usesIfMatch: boolean;
  /** The endpoint may enqueue async work and answer 202 → a `pending` outcome is possible. */
  mayReturn202: boolean;
  /** Past-tense success phrase for the completion toast, e.g. "Benefit suspended". */
  verb: string;
}

export const COMMAND_ACTIONS: Record<CommandActionKey, CommandActionMeta> = {
  // --- Payments (specs/09, specs/12 §12.2) ---
  processContribution: {
    key: "processContribution",
    label: "Process payment",
    requires: "SERVICING_MANAGER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    // Two-phase; 200 may still carry contribution.status=FAILED and 202 means still running.
    // The completion toast is a generic acknowledgement — the screen renders POSTED vs FAILED
    // from its live SOURCE subscription (specs/09 §9.1), never from this return value.
    mayReturn202: true,
    verb: "Payment processed",
  },
  retryContribution: {
    key: "retryContribution",
    label: "Retry payment",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Retry scheduled",
  },

  // --- Benefit lifecycle (specs/10 §10.2–10.3, specs/12 §12.2) ---
  suspendBenefit: {
    key: "suspendBenefit",
    label: "Suspend benefit",
    requires: "SERVICING_MANAGER",
    confirm: true,
    tone: "primary",
    usesIfMatch: true,
    mayReturn202: true,
    verb: "Benefit suspended",
  },
  resumeBenefit: {
    key: "resumeBenefit",
    label: "Resume benefit",
    requires: "SERVICING_MANAGER",
    confirm: true,
    tone: "primary",
    usesIfMatch: true,
    mayReturn202: true,
    verb: "Benefit resumed",
  },
  terminateBenefit: {
    key: "terminateBenefit",
    label: "Terminate benefit",
    requires: "SERVICING_MANAGER",
    confirm: true,
    tone: "danger", // TERMINATED is terminal (specs/06); enqueues cancel-future-contributions
    usesIfMatch: true,
    mayReturn202: true,
    verb: "Benefit terminated",
  },

  // --- Employment cascade (specs/10 §10.4, specs/12 §12.2) ---
  changeEmploymentStatus: {
    key: "changeEmploymentStatus",
    label: "Change employment status",
    requires: "SERVICING_MANAGER",
    confirm: true,
    tone: "primary",
    usesIfMatch: true,
    // Cascades the benefit (LEAVE→suspend, TERMINATED→terminate+cancel-future, ACTIVE→resume);
    // the cancel-future sweep runs async, so treat the outcome as possibly-pending.
    mayReturn202: true,
    verb: "Employment status updated",
  },

  // --- Notes (specs/10 §10.5) — light, append-only, no confirmation ---
  addLoanNote: {
    key: "addLoanNote",
    label: "Add note",
    requires: "OPERATIONS_USER",
    confirm: false,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: false,
    verb: "Note added",
  },

  // --- Exceptions (specs/06 §6.4, specs/12 §12.2) — all servicing roles ---
  createException: {
    key: "createException",
    label: "Create exception",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Exception created",
  },
  assignException: {
    key: "assignException",
    label: "Assign",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Exception assigned",
  },
  markExceptionInReview: {
    key: "markExceptionInReview",
    label: "Mark in review",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Exception moved to review",
  },
  resolveException: {
    key: "resolveException",
    label: "Resolve",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Exception resolved",
  },
  dismissException: {
    key: "dismissException",
    label: "Dismiss",
    requires: "OPERATIONS_USER",
    confirm: true,
    tone: "danger", // DISMISSED is terminal (specs/06 §6.4)
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Exception dismissed",
  },

  // --- Administration (specs/12 §12.3, specs/06 §6.6a) — ADMINISTRATOR only ---
  setEmployerStatus: {
    key: "setEmployerStatus",
    label: "Set employer status",
    requires: "ADMINISTRATOR",
    confirm: true,
    tone: "primary",
    usesIfMatch: false,
    mayReturn202: true,
    verb: "Employer status updated",
  },
  setUserRole: {
    key: "setUserRole",
    label: "Set role",
    requires: "ADMINISTRATOR",
    confirm: true,
    tone: "danger", // privilege change — always confirm deliberately
    usesIfMatch: false,
    mayReturn202: false,
    verb: "Role updated",
  },
};

// ---------------------------------------------------------------------------
// Invokers — a uniform (id, body, opts) adapter over the typed command wrappers
// ---------------------------------------------------------------------------

/**
 * Uniform call shape so a single write-path engine (`useCommand`) can dispatch any action
 * without special-casing wrapper signatures. Each adapter forwards to the correctly-typed
 * `commandClient` wrapper:
 *   - id-only wrappers (process/retry/mark-in-review) ignore `body`;
 *   - the create-exception wrapper has no path id and ignores `id` (its id is server-minted);
 *   - the rest forward `(id, body, opts)`.
 * `body` is narrowed with an `as` cast because the engine passes it opaquely; the concrete
 * request shape is the wrapper's own typed contract (commandTypes.ts).
 */
export const INVOKERS: Record<
  CommandActionKey,
  (id: string, body: unknown, opts: CommandCallOptions) => Promise<CommandOutcome<unknown>>
> = {
  processContribution: (id, _body, opts) => processContribution(id, opts),
  retryContribution: (id, _body, opts) => retryContribution(id, opts),
  suspendBenefit: (id, body, opts) =>
    suspendBenefit(id, body as ReasonRequest | undefined, opts),
  resumeBenefit: (id, body, opts) =>
    resumeBenefit(id, body as ReasonRequest | undefined, opts),
  terminateBenefit: (id, body, opts) =>
    terminateBenefit(id, body as ReasonRequest | undefined, opts),
  changeEmploymentStatus: (id, body, opts) =>
    changeEmploymentStatus(id, body as EmploymentStatusChangeRequest, opts),
  addLoanNote: (id, body, opts) => addLoanNote(id, body as AddNoteRequest, opts),
  createException: (_id, body, opts) =>
    createException(body as CreateExceptionRequest, opts),
  assignException: (id, body, opts) =>
    assignException(id, body as AssignExceptionRequest | undefined, opts),
  markExceptionInReview: (id, _body, opts) => markExceptionInReview(id, opts),
  resolveException: (id, body, opts) =>
    resolveException(id, body as ResolveExceptionRequest, opts),
  dismissException: (id, body, opts) =>
    dismissException(id, body as DismissExceptionRequest, opts),
  setEmployerStatus: (id, body, opts) =>
    setEmployerStatus(id, body as SetEmployerStatusRequest, opts),
  setUserRole: (id, body, opts) => setUserRole(id, body as SetRoleRequest, opts),
};
