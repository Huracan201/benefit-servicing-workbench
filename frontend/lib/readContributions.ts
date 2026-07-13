"use client";

// Payment operations queue read layer (U-C2; specs/04 §4.7, specs/05 §5.6, specs/06 §6.1).
//
// CQRS (specs/02 P7): the web client is READ-ONLY. This hook subscribes (Firebase client SDK)
// to the AUTHORITATIVE `scheduledContributions` SOURCE collection — NOT a projection — so the
// queue reflects the state a command transactionally landed (specs/05 §5.7), and an action taken
// on a row is seen the moment the source doc updates (the row leaves its status tab). The
// eventually-consistent read models (loanWorkbenches / portfolioSummaries) are never read here.
// Every WRITE still goes through the typed command client.
//
// The query is a single status-equality filter + `scheduledDate ASC` — exactly the shape backed
// by the composite index `scheduledContributions (status ASC, scheduledDate ASC, __name__ ASC)`
// (firebase/firestore.indexes.json). The generic useCollectionPage hook enforces the specs/05
// §5.6 subscription rules (bounded limit + indexed predicate + cursor pagination).
//
// TIMESTAMP CAVEAT: raw Firestore docs come back, so `scheduledDate` (and the other date fields)
// arrive as Firestore `Timestamp` objects at runtime — typed here as FirestoreTimestamp
// (`Timestamp | string`). Format them through the Timestamp-aware {@link formatScheduledDate};
// never call string methods on them.

import {
  type DocumentData,
  type QueryConstraint,
  type QueryDocumentSnapshot,
  orderBy,
  where,
} from "firebase/firestore";
import { useMemo } from "react";
import { useCollectionPage } from "@/hooks/useCollectionPage";
import { SCHEDULED_CONTRIBUTIONS } from "@/lib/collectionPaths";
import type { CommandActionKey } from "@/lib/commandActions";
import { SYSTEM_TIMEZONE, type FirestoreTimestamp, type WithId } from "@/lib/readModels";
import type { ContributionStatus, ScheduledContribution } from "@/lib/types";

/** UI table page size (specs/21 §21.1 — 25 rows). */
export const PAYMENTS_PAGE_SIZE = 25;

/** One `scheduledContributions` row plus its Firestore document id. */
export type ContributionRow = WithId<ScheduledContribution>;

/**
 * The two write-path commands the queue offers. Shared by the queue columns and the per-row action
 * cell so the endpoint mapping has one source of truth (see `actionFor`). Wired to the endpoints'
 * real source-state preconditions (specs/09 §9.1–9.2; backend/payments/service.py):
 * `processContribution` advances SCHEDULED **or RETRY_PENDING** → PROCESSING; `retryContribution`
 * advances FAILED-only → RETRY_PENDING. PROCESSING / POSTED / CANCELED offer no action.
 */
export type PaymentAction = Extract<CommandActionKey, "processContribution" | "retryContribution">;

/** Opaque cursor — the last document of the previous page; feed it back to load the next. */
export type ContributionCursor = QueryDocumentSnapshot<DocumentData> | null;

/** One bounded, cursor-paginated page of the payment queue for a single status tab. */
export interface ContributionsPage {
  data: ContributionRow[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
  /** Cursor for the NEXT page (pass back as `cursor`); null when no page is loaded. */
  cursor: ContributionCursor;
  hasMore: boolean;
}

/**
 * Subscribe to one page of `scheduledContributions` filtered to `status`, ordered by
 * `scheduledDate` ascending (index-backed). `cursor` is the previous page's `cursor`.
 * This is a SOURCE-collection read: the rows reflect the state a command has landed
 * (specs/05 §5.7) — never a projection.
 */
export function useContributionsPage(
  status: ContributionStatus,
  cursor: ContributionCursor = null,
): ContributionsPage {
  // useCollectionPage re-subscribes on constraints-array identity, so memoize on the
  // primitive `status` (never a fresh array each render).
  const constraints = useMemo<QueryConstraint[]>(
    () => [where("status", "==", status), orderBy("scheduledDate", "asc")],
    [status],
  );

  const page = useCollectionPage<ContributionRow>(SCHEDULED_CONTRIBUTIONS, {
    constraints,
    pageSize: PAYMENTS_PAGE_SIZE,
    cursor,
  });

  return {
    data: page.items,
    loading: page.loading,
    error: page.error,
    empty: page.empty,
    cursor: page.lastVisible,
    hasMore: page.hasMore,
  };
}

// ---------------------------------------------------------------------------
// Date formatting (SYSTEM_TIMEZONE, never UTC — specs/README). Shared by the queue
// columns and the per-row confirm dialog so a scheduled date renders identically in both.
// ---------------------------------------------------------------------------

/** Coerce a `FirestoreTimestamp` (runtime `Timestamp` or an ISO string) to a `Date`, or null. */
function toDate(ts: FirestoreTimestamp | null | undefined): Date | null {
  if (!ts) return null;
  if (typeof ts === "string") {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const maybe = ts as { toDate?: () => Date };
  if (typeof maybe.toDate === "function") {
    try {
      return maybe.toDate();
    } catch {
      return null;
    }
  }
  return null;
}

// The payment queue spans past (POSTED) and future (SCHEDULED) periods, so the year is
// carried (unlike the portfolio's month/day "next contribution" cell).
const QUEUE_DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
});

/** "Jul 1, 2026" in SYSTEM_TIMEZONE, or an em dash when there is no date. */
export function formatScheduledDate(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? QUEUE_DATE.format(d) : "—";
}
