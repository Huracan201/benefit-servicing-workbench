// The write path (specs/02 P1, specs/11). CQRS: the browser READS Firestore read
// models directly, but every MUTATION goes through this client, which POSTs a business
// command to the Django API. Django owns transactions, state-machine validation,
// invariants, idempotency, and authorization — this module only marshals the request,
// carries the idempotency/optimistic-concurrency headers, follows the 202 async-poll
// contract, and maps failures to a typed `CommandError`.
//
// Framework-light: no React, no client SDK writes. Auth comes from the shared Firebase
// client (`getFirebaseAuth().currentUser.getIdToken()`).
//
// IMPORTANT idempotency invariant (specs/08): one Idempotency-Key is minted per logical
// command and REUSED verbatim on every poll/retry — NEVER regenerated. Regenerating it
// would let the same intent execute twice. Callers retrying after a transport failure
// SHOULD pass the SAME `idempotencyKey` they used before.
//
// IMPORTANT outcome invariant (specs/02, specs/05): a 202 body is ADVISORY. The
// authoritative result of a command is the mutated entity itself — the UI should render
// from its Firestore subscription and treat this client's return value as a
// submit-acknowledgement, not the source of truth.

import { getFirebaseAuth } from "@/lib/firebase";
import type {
  ActivateRequest,
  AddNoteRequest,
  AssignExceptionRequest,
  CommandBenefitAgreement,
  CommandContribution,
  CommandNote,
  CommandOperationalException,
  CommandUser,
  CreateExceptionRequest,
  DismissExceptionRequest,
  EmploymentChangeResult,
  EmploymentStatusChangeRequest,
  OperationStatus,
  ProcessContributionResult,
  ReasonRequest,
  ResolveExceptionRequest,
  SetEmployerStatusRequest,
  SetEmployerStatusResult,
  SetRoleRequest,
} from "@/lib/commandTypes";
import {
  CommandError,
  isServerErrorCode,
  statusToFallbackCode,
} from "@/lib/errors";
import type { Role } from "@/lib/types";

// ---------------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------------

/** Max number of poll re-POSTs after the initial request before giving up to "pending". */
const DEFAULT_MAX_POLLS = 5;
/** Fallback poll delay when neither the header nor the body carries a hint. */
const DEFAULT_RETRY_AFTER_SECONDS = 2;
/** Clamp so a misbehaving `Retry-After` can never freeze the UI. */
const MAX_RETRY_AFTER_SECONDS = 30;

function apiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  return base.replace(/\/+$/, "");
}

function buildUrl(path: string): string {
  return `${apiBaseUrl()}/api/v1/${path.replace(/^\/+/, "")}`;
}

// ---------------------------------------------------------------------------
// Public option / result types
// ---------------------------------------------------------------------------

export interface CommandCallOptions {
  /**
   * Reuse across retries of the SAME intent; generated (uuid) if omitted. Passing your
   * own lets a user-visible "retry" button re-submit safely (specs/08).
   */
  idempotencyKey?: string;
  /** Sent as `If-Match` for stale-write protection; mismatch → 409 STALE_WRITE. */
  expectedRevision?: number;
  /** Trace id echoed into server events/logs; generated server-side if omitted. */
  correlationId?: string;
  /**
   * The caller's role — NOT enforced here (the server authorizes). Exposed so UI code
   * can pass it for local affordance/telemetry.
   */
  role?: Role;
  /** Abort the request (and any in-flight poll wait). */
  signal?: AbortSignal;
  /** Override the poll budget (default 5). */
  maxPolls?: number;
}

export interface SendCommandInit extends CommandCallOptions {
  body?: unknown;
}

/**
 * The command settled with a final (2xx, non-202) response. `data` is advisory — prefer
 * the entity's Firestore subscription for authoritative state.
 */
export interface CommandCompleted<TRes> {
  status: "completed";
  httpStatus: number;
  data: TRes;
  idempotencyKey: string;
  correlationId?: string;
}

/**
 * The poll budget was exhausted while the async op was still running (repeated 202).
 * NOT an error — the op is progressing; observe the entity via Firestore to see it land.
 */
export interface CommandPending {
  status: "pending";
  operation: OperationStatus | null;
  idempotencyKey: string;
  correlationId?: string;
}

export type CommandOutcome<TRes> = CommandCompleted<TRes> | CommandPending;

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function newIdempotencyKey(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  // Fallback for environments without WebCrypto — collision-resistant enough for a key.
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** Promise that resolves after `ms`, or rejects immediately if the signal aborts. */
function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    let timer: ReturnType<typeof setTimeout>;
    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("Aborted", "AbortError"));
    };
    timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, ms));
    signal?.addEventListener("abort", onAbort);
  });
}

async function resolveToken(): Promise<string> {
  try {
    const user = getFirebaseAuth().currentUser;
    if (!user) {
      throw new CommandError({ code: "UNAUTHENTICATED", serverMessage: "No signed-in user." });
    }
    return await user.getIdToken();
  } catch (error) {
    if (error instanceof CommandError) throw error;
    throw new CommandError({
      code: "UNAUTHENTICATED",
      serverMessage: error instanceof Error ? error.message : "Could not obtain an ID token.",
    });
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function asOperationStatus(value: unknown): OperationStatus | null {
  // The server's 202 body carries `status` (e.g. "IN_PROGRESS"), not `state` — matching
  // commands.base.OperationInProgress.to_body / openapi OperationStatus.
  if (value && typeof value === "object" && "status" in value) {
    return value as OperationStatus;
  }
  return null;
}

function correlationIdFrom(response: Response, body: unknown): string | undefined {
  const header = response.headers.get("X-Correlation-Id");
  if (header) return header;
  if (body && typeof body === "object" && "correlationId" in body) {
    const cid = (body as { correlationId?: unknown }).correlationId;
    if (typeof cid === "string") return cid;
  }
  return undefined;
}

function parseHeaderInt(raw: string | null): number | null {
  if (raw == null) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function resolveRetryAfterSeconds(response: Response, op: OperationStatus | null): number {
  const fromHeader = parseHeaderInt(response.headers.get("Retry-After"));
  const seconds =
    fromHeader ??
    (typeof op?.retryAfter === "number" && op.retryAfter >= 0
      ? op.retryAfter
      : DEFAULT_RETRY_AFTER_SECONDS);
  return Math.min(seconds, MAX_RETRY_AFTER_SECONDS);
}

async function toCommandError(response: Response): Promise<CommandError> {
  const body = await readJson(response);
  let code = statusToFallbackCode(response.status);
  let serverMessage: string | null = null;
  let correlationId: string | null = response.headers.get("X-Correlation-Id");

  if (body && typeof body === "object" && "error" in body) {
    const envelope = (body as { error?: unknown }).error;
    if (envelope && typeof envelope === "object") {
      const e = envelope as { code?: unknown; message?: unknown; correlationId?: unknown };
      if (isServerErrorCode(e.code)) code = e.code;
      if (typeof e.message === "string") serverMessage = e.message;
      if (typeof e.correlationId === "string") correlationId = e.correlationId;
    }
  }

  return new CommandError({
    code,
    httpStatus: response.status,
    serverMessage,
    correlationId,
    retryAfterSeconds:
      response.status === 429 ? parseHeaderInt(response.headers.get("Retry-After")) : null,
  });
}

// ---------------------------------------------------------------------------
// Core send + poll
// ---------------------------------------------------------------------------

/**
 * POST a business command to `/api/v1/{path}` and follow the async contract.
 *
 * - Sends `Authorization: Bearer <Firebase ID token>`, `Idempotency-Key`,
 *   `Content-Type: application/json`, and (when `expectedRevision` is given) `If-Match`.
 * - On 202 + `Retry-After`, waits and RE-POSTs the IDENTICAL request with the SAME
 *   Idempotency-Key, up to `maxPolls` times, then returns a `pending` outcome.
 * - On any other 2xx, returns `completed` with the (advisory) parsed body.
 * - On any 4xx/5xx, throws a typed {@link CommandError}.
 *
 * @typeParam TRes - the command's success body (see commandTypes.ts).
 */
export async function sendCommand<TRes>(
  path: string,
  init: SendCommandInit = {},
): Promise<CommandOutcome<TRes>> {
  const { body, expectedRevision, correlationId, signal } = init;
  // Normalize the poll budget to a safe finite integer: a negative/NaN value would make
  // the loop never run (no request sent), and Infinity could poll forever on 202s.
  const maxPolls =
    typeof init.maxPolls === "number" && Number.isFinite(init.maxPolls)
      ? Math.max(0, Math.floor(init.maxPolls))
      : DEFAULT_MAX_POLLS;
  const idempotencyKey = init.idempotencyKey ?? newIdempotencyKey();
  const token = await resolveToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "Idempotency-Key": idempotencyKey,
  };
  if (expectedRevision != null) headers["If-Match"] = String(expectedRevision);
  if (correlationId) headers["X-Correlation-Id"] = correlationId;

  // The exact request object is reused for every poll re-POST — identical bytes,
  // identical Idempotency-Key (the whole point of the async contract, specs/08).
  const requestInit: RequestInit = {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  };

  const url = buildUrl(path);
  let lastOperation: OperationStatus | null = null;

  for (let poll = 0; poll <= maxPolls; poll++) {
    let response: Response;
    try {
      response = await fetch(url, requestInit);
    } catch (error) {
      if (isAbortError(error)) throw error; // caller-initiated abort — surface as-is
      throw new CommandError({
        code: "NETWORK_ERROR",
        serverMessage: error instanceof Error ? error.message : null,
        // Hand the resolved key back: the server may already have accepted the command,
        // so a retry MUST reuse this key (specs/08), never mint a fresh one.
        idempotencyKey,
      });
    }

    if (response.status === 202) {
      lastOperation = asOperationStatus(await readJson(response));
      if (poll < maxPolls) {
        await delay(resolveRetryAfterSeconds(response, lastOperation) * 1000, signal);
        continue;
      }
      return {
        status: "pending",
        operation: lastOperation,
        idempotencyKey,
        correlationId: lastOperation?.correlationId,
      };
    }

    if (response.ok) {
      const data = await readJson(response);
      return {
        status: "completed",
        httpStatus: response.status,
        data: data as TRes,
        idempotencyKey,
        correlationId: correlationIdFrom(response, data),
      };
    }

    throw await toCommandError(response);
  }

  // Unreachable (the loop always returns or throws); satisfies the type checker.
  return { status: "pending", operation: lastOperation, idempotencyKey };
}

// ---------------------------------------------------------------------------
// Typed command wrappers (what feature slices import)
//
// Each is a thin, correctly-typed call into `sendCommand`. Path segments match
// backend/api/urls.py; response generics match the openapi result schemas.
// ---------------------------------------------------------------------------

const withBody = (opts: CommandCallOptions | undefined, body: unknown): SendCommandInit => ({
  ...opts,
  body,
});

// --- Benefit agreements (specs/10 §10.1–10.3) ---

export function activateBenefit(
  agreementId: string,
  body?: ActivateRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandBenefitAgreement>> {
  return sendCommand<CommandBenefitAgreement>(
    `benefit-agreements/${encodeURIComponent(agreementId)}/activate`,
    withBody(opts, body),
  );
}

export function suspendBenefit(
  agreementId: string,
  body?: ReasonRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandBenefitAgreement>> {
  return sendCommand<CommandBenefitAgreement>(
    `benefit-agreements/${encodeURIComponent(agreementId)}/suspend`,
    withBody(opts, body),
  );
}

export function resumeBenefit(
  agreementId: string,
  body?: ReasonRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandBenefitAgreement>> {
  return sendCommand<CommandBenefitAgreement>(
    `benefit-agreements/${encodeURIComponent(agreementId)}/resume`,
    withBody(opts, body),
  );
}

export function terminateBenefit(
  agreementId: string,
  body?: ReasonRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandBenefitAgreement>> {
  return sendCommand<CommandBenefitAgreement>(
    `benefit-agreements/${encodeURIComponent(agreementId)}/terminate`,
    withBody(opts, body),
  );
}

// --- Employment (specs/10 §10.4) ---

export function changeEmploymentStatus(
  borrowerId: string,
  body: EmploymentStatusChangeRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<EmploymentChangeResult>> {
  return sendCommand<EmploymentChangeResult>(
    `borrowers/${encodeURIComponent(borrowerId)}/employment-status`,
    withBody(opts, body),
  );
}

// --- Contributions (specs/09) ---

export function processContribution(
  contributionId: string,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<ProcessContributionResult>> {
  return sendCommand<ProcessContributionResult>(
    `contributions/${encodeURIComponent(contributionId)}/process`,
    { ...opts },
  );
}

export function retryContribution(
  contributionId: string,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandContribution>> {
  return sendCommand<CommandContribution>(
    `contributions/${encodeURIComponent(contributionId)}/retry`,
    { ...opts },
  );
}

// --- Exceptions (specs/09 §9.3, specs/06 §6.4) ---

export function createException(
  body: CreateExceptionRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandOperationalException>> {
  return sendCommand<CommandOperationalException>("exceptions", withBody(opts, body));
}

export function assignException(
  exceptionId: string,
  body?: AssignExceptionRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandOperationalException>> {
  return sendCommand<CommandOperationalException>(
    `exceptions/${encodeURIComponent(exceptionId)}/assign`,
    withBody(opts, body),
  );
}

export function markExceptionInReview(
  exceptionId: string,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandOperationalException>> {
  return sendCommand<CommandOperationalException>(
    `exceptions/${encodeURIComponent(exceptionId)}/mark-in-review`,
    { ...opts },
  );
}

export function resolveException(
  exceptionId: string,
  body: ResolveExceptionRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandOperationalException>> {
  return sendCommand<CommandOperationalException>(
    `exceptions/${encodeURIComponent(exceptionId)}/resolve`,
    withBody(opts, body),
  );
}

export function dismissException(
  exceptionId: string,
  body: DismissExceptionRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandOperationalException>> {
  return sendCommand<CommandOperationalException>(
    `exceptions/${encodeURIComponent(exceptionId)}/dismiss`,
    withBody(opts, body),
  );
}

// --- Notes (specs/10 §10.5) ---

export function addLoanNote(
  loanId: string,
  body: AddNoteRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandNote>> {
  return sendCommand<CommandNote>(
    `loans/${encodeURIComponent(loanId)}/notes`,
    withBody(opts, body),
  );
}

// --- Admin (specs/12) ---

export function setUserRole(
  uid: string,
  body: SetRoleRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<CommandUser>> {
  return sendCommand<CommandUser>(
    `admin/users/${encodeURIComponent(uid)}/role`,
    withBody(opts, body),
  );
}

export function setEmployerStatus(
  employerId: string,
  body: SetEmployerStatusRequest,
  opts?: CommandCallOptions,
): Promise<CommandOutcome<SetEmployerStatusResult>> {
  return sendCommand<SetEmployerStatusResult>(
    `admin/employers/${encodeURIComponent(employerId)}/status`,
    withBody(opts, body),
  );
}
