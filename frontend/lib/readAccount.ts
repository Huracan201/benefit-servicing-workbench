"use client";

// Account read layer for the loan / benefit DETAIL screen (specs/04, specs/05, specs/08).
//
// CQRS (specs/02 P7): the web client is READ-ONLY. These hooks subscribe (Firebase client SDK)
// to the authoritative SOURCE documents — loans, borrowers, benefitAgreements, the schedule +
// its attempts subcollection, operationalExceptions, and the loan-scoped servicingEvents mirror.
// They deliberately do NOT read the eventually-consistent projections (loanWorkbenches /
// portfolioSummaries / employerSummaries): those lag a completed command by seconds (specs/05
// §5.7), so a detail screen that must reflect a just-issued command reads the source docs the
// command transactionally updated. Every WRITE still goes through the typed command client.
//
// TIMESTAMP CAVEAT (read before consuming a date field): these hooks return the RAW Firestore
// documents, so every date-time field arrives as a Firestore `Timestamp` object. The Command*
// return types (from lib/commandTypes) declare those fields as ISO strings because they mirror
// the JSON command RESPONSES, not the stored docs. So `contribution.scheduledDate`,
// `attempt.startedAt`, `note.createdAt`, `agreement.startDate`, `exception.lastSeenAt`, etc. are
// `Timestamp` at runtime, not `string`. Consumers MUST format them through a Timestamp-aware
// helper and must not call string methods on them. The non-date fields (ids, statuses, `*Cents`
// amounts, `revision`, counts) match the stored docs exactly.
//
// Subscription rules (specs/05 §5.6) are enforced by the generic hooks: every list carries a
// bounded `limit` and an index-backed predicate. Each hook takes a nullable id and is fully
// DISABLED (no subscription) when the id is null, so the detail screen can chain them
// (loan → borrower / agreement → schedule → attempts) as ids resolve.

import { type QueryConstraint, orderBy, where } from "firebase/firestore";
import { useMemo } from "react";
import {
  type CollectionPageState,
  useCollectionPage,
} from "@/hooks/useCollectionPage";
import { useDocument } from "@/hooks/useDocument";
import {
  BENEFIT_AGREEMENTS,
  BORROWERS,
  LOANS,
  OPERATIONAL_EXCEPTIONS,
  SCHEDULED_CONTRIBUTIONS,
  attemptsPath,
  loanEventsPath,
  notesPath,
} from "@/lib/collectionPaths";
import type {
  CommandBenefitAgreement,
  CommandContribution,
  CommandLoan,
  CommandNote,
  CommandOperationalException,
  CommandPaymentAttempt,
} from "@/lib/commandTypes";
import type { ReadModelDoc, ReadModelList, WithId } from "@/lib/readModels";
import type { Borrower, ServicingEvent } from "@/lib/types";

// ---------------------------------------------------------------------------
// Bounds (specs/05 §5.6 — every list subscription is limited). These are single-
// page reads for a detail screen; the per-loan cardinalities all fit one page.
// ---------------------------------------------------------------------------

/** A benefit schedule is `termMonths` rows (MVP ≤ 36); 200 is the hook's hard ceiling. */
const SCHEDULE_PAGE_SIZE = 200;
/** A contribution accrues few attempts (retries are bounded — specs/09). */
const ATTEMPTS_PAGE_SIZE = 50;
/** Per-loan open+historical exceptions are few; generous bound for the client-side sort. */
const EXCEPTIONS_PAGE_SIZE = 100;
/** The detail timeline shows the recent tail (specs/05 §5.6). */
const EVENTS_LIMIT = 50;
/** Recent manual notes for the loan. */
const NOTES_PAGE_SIZE = 100;

/** Syntactically-valid placeholder parent id for a DISABLED subcollection hook. The query is
 *  gated off by `enabled: false`, so this path is never actually read; it only keeps the built
 *  subcollection path well-formed (never `loans//events`). */
const DISABLED_PARENT = "__disabled__";

function toList<T>(page: CollectionPageState<T>): ReadModelList<T> {
  return {
    data: page.items,
    loading: page.loading,
    error: page.error,
    empty: page.empty,
  };
}

// ---------------------------------------------------------------------------
// Single-document subscriptions (specs/05 §5.6). Null id → idle/empty.
// ---------------------------------------------------------------------------

/** `loans/{loanId}` — the authoritative loan (specs/04 §4.5). Carries `revision` for If-Match on
 *  loan-scoped commands. Note: this lean shape omits denormalized display mirrors present on the
 *  stored doc (borrowerName, servicerName, nextContributionDate…); source those from the borrower
 *  / agreement docs. */
export function useLoanDoc(loanId: string | null): ReadModelDoc<CommandLoan> {
  return useDocument<CommandLoan>(LOANS, loanId);
}

/** `borrowers/{borrowerId}` — the authoritative borrower (specs/04 §4.4). Pass
 *  `loan.borrowerId`; the borrower→loan link is canonical on the loan (specs/04 §4.4). */
export function useBorrowerDoc(borrowerId: string | null): ReadModelDoc<Borrower> {
  return useDocument<Borrower>(BORROWERS, borrowerId);
}

/** `benefitAgreements/{agreementId}` — the authoritative agreement (specs/04 §4.6). Exposes
 *  `revision` for the `If-Match` optimistic-concurrency guard on suspend / resume / terminate /
 *  activate commands (specs/08). Pass `loan.benefitAgreementId` (null for a loan with no
 *  benefit → idle). */
export function useBenefitAgreementDoc(
  agreementId: string | null,
): ReadModelDoc<CommandBenefitAgreement> {
  return useDocument<CommandBenefitAgreement>(BENEFIT_AGREEMENTS, agreementId);
}

// ---------------------------------------------------------------------------
// Collection subscriptions (bounded + index-backed — specs/05 §5.6, specs/13).
// ---------------------------------------------------------------------------

/** The full schedule for an agreement, ordered by `installmentNumber` ascending
 *  (index: `benefitAgreementId ASC, installmentNumber ASC`). Null id → idle/empty. */
export function useContributionsForAgreement(
  agreementId: string | null,
): ReadModelList<CommandContribution> {
  const enabled = Boolean(agreementId);
  const constraints = useMemo<QueryConstraint[]>(
    () =>
      agreementId
        ? [
            where("benefitAgreementId", "==", agreementId),
            orderBy("installmentNumber", "asc"),
          ]
        : [],
    [agreementId],
  );
  const page = useCollectionPage<CommandContribution>(SCHEDULED_CONTRIBUTIONS, {
    constraints,
    pageSize: SCHEDULE_PAGE_SIZE,
    enabled,
  });
  return toList(page);
}

/** The attempts subcollection for one contribution, ordered by `attemptNumber` ascending
 *  (`scheduledContributions/{id}/attempts`; single-field auto-index). Null id → idle/empty. */
export function useAttemptsForContribution(
  contributionId: string | null,
): ReadModelList<CommandPaymentAttempt> {
  const enabled = Boolean(contributionId);
  const path = attemptsPath(contributionId ?? DISABLED_PARENT);
  const constraints = useMemo<QueryConstraint[]>(
    () => [orderBy("attemptNumber", "asc")],
    [],
  );
  const page = useCollectionPage<CommandPaymentAttempt>(path, {
    constraints,
    pageSize: ATTEMPTS_PAGE_SIZE,
    enabled,
  });
  return toList(page);
}

/**
 * Operational exceptions in this LOAN's context, most severe first. Pass the loan id: the query
 * filters by the exception's `loanId` field (`where loanId ==`; single-field auto-index), which
 * surfaces payment failures on the loan's contributions plus any loan-scoped exception.
 *
 * There is no `(loanId, severityRank)` composite index, so `severityRank DESC` is applied
 * CLIENT-SIDE over the bounded page (permitted for a small per-loan set — specs/05 §5.6). A
 * stable id tiebreak keeps the order from churning between snapshots. Null id → idle/empty.
 */
export function useExceptionsForEntity(
  loanId: string | null,
): ReadModelList<CommandOperationalException> {
  const enabled = Boolean(loanId);
  const constraints = useMemo<QueryConstraint[]>(
    () => (loanId ? [where("loanId", "==", loanId)] : []),
    [loanId],
  );
  const page = useCollectionPage<CommandOperationalException>(
    OPERATIONAL_EXCEPTIONS,
    { constraints, pageSize: EXCEPTIONS_PAGE_SIZE, enabled },
  );
  const data = useMemo(
    () =>
      [...page.items].sort(
        (a, b) =>
          b.severityRank - a.severityRank ||
          (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
      ),
    [page.items],
  );
  return { data, loading: page.loading, error: page.error, empty: page.empty };
}

/**
 * The recent servicing-event tail for a loan, newest first. Pass the loan id: reads the
 * `loans/{loanId}/events` mirror (specs/04 §4.9) ordered by `createdAt DESC, sequence DESC`
 * (index: `events (createdAt DESC, sequence DESC)`) — `sequence` is the tiebreak for events that
 * share a `createdAt` within one command (specs/04 §4.9). Bounded to the recent tail. Null id →
 * idle/empty.
 */
export function useEventsForEntity(
  loanId: string | null,
): ReadModelList<WithId<ServicingEvent>> {
  const enabled = Boolean(loanId);
  const path = loanEventsPath(loanId ?? DISABLED_PARENT);
  const constraints = useMemo<QueryConstraint[]>(
    () => [orderBy("createdAt", "desc"), orderBy("sequence", "desc")],
    [],
  );
  const page = useCollectionPage<WithId<ServicingEvent>>(path, {
    constraints,
    pageSize: EVENTS_LIMIT,
    enabled,
  });
  return toList(page);
}

/** `loans/{loanId}/notes` — manual notes, newest first (`createdAt DESC`; single-field
 *  auto-index). Docs store `text` (not `body`) — `CommandNote` is the correct shape. Null id →
 *  idle/empty. */
export function useNotesForLoan(
  loanId: string | null,
): ReadModelList<CommandNote> {
  const enabled = Boolean(loanId);
  const path = notesPath(loanId ?? DISABLED_PARENT);
  const constraints = useMemo<QueryConstraint[]>(
    () => [orderBy("createdAt", "desc")],
    [],
  );
  const page = useCollectionPage<CommandNote>(path, {
    constraints,
    pageSize: NOTES_PAGE_SIZE,
    enabled,
  });
  return toList(page);
}
