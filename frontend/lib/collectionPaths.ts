// Firestore SOURCE-collection paths for the account / detail read layer (specs/04, specs/05).
//
// CQRS (specs/02 P7): the workbench detail screen READS these authoritative source docs — to
// render account state and to confirm a command actually landed. It must NEVER read the
// eventually-consistent projections (loanWorkbenches / portfolioSummaries / employerSummaries)
// to confirm a write or to make a financial decision; those lag a completed command by seconds
// (specs/05 §5.7). Post-command truth comes only from the transactionally-updated source docs
// below.
//
// Every path is read-allowed for any authenticated servicing user in firebase/firestore.rules,
// and the string values are kept in lockstep with the backend's repositories/refs.py. Firestore
// is deny-by-default: a path not present in the rules is unreadable. If a screen needs a path
// that is not here, add it to the rules first — never substitute a projection read.

/** `loans/{loanId}` — authoritative loan (specs/04 §4.5). */
export const LOANS = "loans" as const;

/** `borrowers/{borrowerId}` — authoritative borrower (specs/04 §4.4). */
export const BORROWERS = "borrowers" as const;

/** `benefitAgreements/{agreementId}` — authoritative benefit agreement (specs/04 §4.6). */
export const BENEFIT_AGREEMENTS = "benefitAgreements" as const;

/** `scheduledContributions/{contributionId}` — the schedule + per-installment payment state
 *  (specs/04 §4.7). */
export const SCHEDULED_CONTRIBUTIONS = "scheduledContributions" as const;

/** `operationalExceptions/{exceptionId}` — the operational-exception source (specs/04 §4.10). */
export const OPERATIONAL_EXCEPTIONS = "operationalExceptions" as const;

/** `servicingEvents/{eventId}` — the GLOBAL, cross-entity audit stream (specs/04 §4.9). For a
 *  single entity's timeline prefer the loan-scoped mirror ({@link loanEventsPath}), which is
 *  index-backed and cheaper than filtering this global stream (specs/05 §5.6). */
export const SERVICING_EVENTS = "servicingEvents" as const;

// ---------------------------------------------------------------------------
// Subcollection path builders — these MATCH firebase/firestore.rules exactly.
// Firestore accepts a slash-delimited collection path (odd segment count), so the
// generic useDocument / useCollectionPage hooks consume these strings directly.
// ---------------------------------------------------------------------------

/**
 * `scheduledContributions/{contributionId}/attempts` — the authoritative payment-attempt store
 * (specs/04 §4.8; rules: `scheduledContributions/{id}/attempts/{attemptId}`). Attempts live ONLY
 * here — there is no top-level attempts collection (specs/04 §4.1 "Change from v1").
 */
export function attemptsPath(contributionId: string): string {
  return `${SCHEDULED_CONTRIBUTIONS}/${contributionId}/attempts`;
}

/**
 * `loans/{loanId}/notes` — manual servicing notes, append-only (specs/04 §4.1, §4.12a;
 * rules: `loans/{loanId}/notes/{noteId}`).
 */
export function notesPath(loanId: string): string {
  return `${LOANS}/${loanId}/notes`;
}

/**
 * `loans/{loanId}/events` — the loan-scoped servicing-event mirror (specs/04 §4.9;
 * rules: `loans/{loanId}/events/{eventId}`). Every event carrying this `loanId` (benefit,
 * payment, contribution, and manual-note events) is mirrored here in the SAME transaction as the
 * global `servicingEvents` write, so this subcollection is the complete per-loan timeline. It is
 * the spec-blessed per-entity feed (specs/05 §5.6) and is served by the dedicated
 * `events (createdAt DESC, sequence DESC)` composite index (firebase/firestore.indexes.json).
 */
export function loanEventsPath(loanId: string): string {
  return `${LOANS}/${loanId}/events`;
}
