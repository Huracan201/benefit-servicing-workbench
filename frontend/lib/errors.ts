// Command-API error taxonomy + human-facing copy (specs/11, specs/08, specs/12).
//
// The Django command layer returns a typed envelope on failure:
//   { "error": { "code": ErrorCode, "message": string, "correlationId"?: string } }
// (openapi.yaml `Error` schema). This module models that envelope, adds the few
// transport-level codes the envelope can't carry (network/rate-limit/5xx), and maps
// every code to short, operator-facing copy the UI can show verbatim. It is pure and
// framework-free — no React, no fetch — so it can be unit-tested and reused anywhere.

// ---------------------------------------------------------------------------
// Error codes
// ---------------------------------------------------------------------------

/**
 * Codes the server can put in the error envelope (openapi.yaml `ErrorCode`).
 * Keep in exact sync with the backend enum — string values are the contract.
 */
export const SERVER_ERROR_CODES = [
  "IDEMPOTENCY_KEY_REQUIRED",
  "VALIDATION_ERROR",
  "UNAUTHENTICATED",
  "FORBIDDEN",
  "NOT_FOUND",
  "INVALID_TRANSITION",
  "INVARIANT_VIOLATION",
  "IDEMPOTENCY_KEY_REUSED",
  "STALE_WRITE",
  "BENEFIT_NOT_ACCEPTING_PAYMENTS",
  "UNPROCESSABLE",
] as const;
export type ServerErrorCode = (typeof SERVER_ERROR_CODES)[number];

/**
 * Synthetic codes the client assigns when there is no usable envelope: a 429, a 5xx,
 * a dropped connection, a still-running async op, or an unclassifiable failure.
 */
export const CLIENT_ERROR_CODES = [
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "NETWORK_ERROR",
  "IN_PROGRESS",
  "UNKNOWN",
] as const;
export type ClientErrorCode = (typeof CLIENT_ERROR_CODES)[number];

/** The full set of codes callers may switch on. */
export type ErrorCode = ServerErrorCode | ClientErrorCode;

const SERVER_ERROR_CODE_SET: ReadonlySet<string> = new Set(SERVER_ERROR_CODES);

/** True when `value` is a code the server is allowed to emit in the envelope. */
export function isServerErrorCode(value: unknown): value is ServerErrorCode {
  return typeof value === "string" && SERVER_ERROR_CODE_SET.has(value);
}

// ---------------------------------------------------------------------------
// Human-facing copy
// ---------------------------------------------------------------------------

/**
 * Short, operator-facing copy per code. Written to be shown verbatim in a toast or
 * inline error — no codes, no stack traces. STALE_WRITE deliberately tells the user to
 * refresh, since the fix is to reload the (Firestore-live) record and reissue.
 */
const HUMAN_MESSAGES: Record<ErrorCode, string> = {
  // --- server codes ---
  IDEMPOTENCY_KEY_REQUIRED: "Something went wrong preparing this request. Please try again.",
  VALIDATION_ERROR: "Some details are missing or invalid. Check the form and try again.",
  UNAUTHENTICATED: "Your session has expired. Sign in again to continue.",
  FORBIDDEN: "You don't have permission to do this.",
  NOT_FOUND: "We couldn't find that record — it may have been removed.",
  INVALID_TRANSITION:
    "This action isn't available in the record's current state. Refresh and try again.",
  INVARIANT_VIOLATION: "This action would break a data rule and was stopped. Refresh and review.",
  IDEMPOTENCY_KEY_REUSED:
    "This request was already submitted. Check the record before retrying.",
  STALE_WRITE: "This record changed — refresh and retry.",
  BENEFIT_NOT_ACCEPTING_PAYMENTS: "This benefit isn't accepting payments right now.",
  UNPROCESSABLE: "This action can't be completed given the current data.",
  // --- client / transport codes ---
  RATE_LIMITED: "Too many requests. Wait a moment and try again.",
  INTERNAL_ERROR: "Something went wrong on our end. Please try again.",
  NETWORK_ERROR: "Couldn't reach the server. Check your connection and try again.",
  IN_PROGRESS: "Still processing — this will update automatically.",
  UNKNOWN: "Something went wrong. Please try again.",
};

/** Operator-facing copy for a code; always defined (falls back to UNKNOWN's copy). */
export function humanMessageForCode(code: ErrorCode): string {
  return HUMAN_MESSAGES[code] ?? HUMAN_MESSAGES.UNKNOWN;
}

/**
 * Codes where an unchanged retry (same Idempotency-Key) is reasonable — transient
 * transport/availability failures, not business rejections. Affordance only.
 */
export const RETRIABLE_ERROR_CODES: ReadonlySet<ErrorCode> = new Set<ErrorCode>([
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "NETWORK_ERROR",
  "IN_PROGRESS",
]);

/** Whether the UI should offer a plain "retry" affordance for this code. */
export function isRetriable(code: ErrorCode): boolean {
  return RETRIABLE_ERROR_CODES.has(code);
}

/**
 * Best-effort code when no envelope is available — derive from the HTTP status.
 * 409 is intentionally UNKNOWN: the server always carries a *typed* conflict code in
 * the envelope, so a 409 with no envelope is genuinely unclassifiable.
 */
export function statusToFallbackCode(status: number): ErrorCode {
  switch (status) {
    case 400:
      return "VALIDATION_ERROR";
    case 401:
      return "UNAUTHENTICATED";
    case 403:
      return "FORBIDDEN";
    case 404:
      return "NOT_FOUND";
    case 422:
      return "UNPROCESSABLE";
    case 429:
      return "RATE_LIMITED";
    default:
      return status >= 500 ? "INTERNAL_ERROR" : "UNKNOWN";
  }
}

// ---------------------------------------------------------------------------
// CommandError
// ---------------------------------------------------------------------------

export interface CommandErrorInit {
  code: ErrorCode;
  /** HTTP status that produced this error, or null for pre-flight/transport failures. */
  httpStatus?: number | null;
  /** Raw server-provided message (may be technical); prefer `userMessage` for display. */
  serverMessage?: string | null;
  correlationId?: string | null;
  /** Seconds hinted by a `Retry-After` header (e.g. on 429). */
  retryAfterSeconds?: number | null;
  /**
   * The Idempotency-Key the failed command carried, when known. Preserved on transport
   * failures so a caller can retry with the SAME key (specs/08) — a fresh key could
   * replay a mutation the server may already have accepted.
   */
  idempotencyKey?: string | null;
}

/**
 * The single error type every command rejection uses. `userMessage` is safe to render;
 * `code` is safe to switch on; `correlationId` ties a report back to the server logs.
 */
export class CommandError extends Error {
  readonly code: ErrorCode;
  readonly httpStatus: number | null;
  readonly serverMessage: string | null;
  readonly userMessage: string;
  readonly correlationId: string | null;
  readonly retryAfterSeconds: number | null;
  readonly idempotencyKey: string | null;

  constructor(init: CommandErrorInit) {
    const userMessage = humanMessageForCode(init.code);
    super(init.serverMessage ?? userMessage);
    this.name = "CommandError";
    this.code = init.code;
    this.httpStatus = init.httpStatus ?? null;
    this.serverMessage = init.serverMessage ?? null;
    this.userMessage = userMessage;
    this.correlationId = init.correlationId ?? null;
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.idempotencyKey = init.idempotencyKey ?? null;
    // Restore the prototype chain so `instanceof CommandError` survives transpilation.
    Object.setPrototypeOf(this, CommandError.prototype);
  }

  get retriable(): boolean {
    return isRetriable(this.code);
  }
}

/** Narrowing helper for `catch` blocks. */
export function isCommandError(value: unknown): value is CommandError {
  return value instanceof CommandError;
}
