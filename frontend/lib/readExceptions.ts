"use client";

// Exception-workbench read layer (specs/04 §4.10, specs/05 §5.6, specs/13). CQRS
// (specs/02 P7): the workbench READS the authoritative `operationalExceptions` SOURCE
// collection directly (firebase/firestore.rules allows any servicing user to read it) —
// never a projection. Post-command truth (a status flip, a new assignee) comes from this
// live subscription on the source doc, which the transactional command layer updates in
// the same transaction; a projection would lag by seconds (specs/05 §5.7).
//
// The subscription is index-backed + bounded + cursor-paginated (the useCollectionPage
// contract). There is exactly ONE composite index that carries the severity ordering
// (`status ASC, severityRank DESC, createdAt DESC`), so the default queue orders by
// severity then recency. A single non-status equality predicate (exceptionType) is
// supported by its own `exceptionType ASC, status ASC, createdAt DESC` index — but that
// index cannot ALSO order by severityRank, so the type-filtered view orders by recency
// only. Only one non-status equality predicate is ever applied at a time (the composite
// indexes admit no two-equality-plus-severity shape); the builder below enforces that.

import {
  type DocumentData,
  type QueryConstraint,
  type QueryDocumentSnapshot,
  orderBy,
  where,
} from "firebase/firestore";
import { useMemo } from "react";
import {
  type CollectionPageState,
  useCollectionPage,
} from "@/hooks/useCollectionPage";
import { OPERATIONAL_EXCEPTIONS } from "@/lib/collectionPaths";
import { SYSTEM_TIMEZONE } from "@/lib/readModels";
import type {
  ExceptionStatus,
  ExceptionType,
  FirestoreTimestamp,
  OperationalException,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Row + query shapes
// ---------------------------------------------------------------------------

/** An operational-exception source doc plus its Firestore id (the generic page hook
 *  spreads `{ id, ...data }`). The id is the deterministic `{entityId}__{exceptionType}`
 *  for auto-exceptions, or an auto-id for manually-created ones (specs/04 §4.10). */
export type ExceptionRow = OperationalException & { id: string };

/** Opaque cursor: the last document of the previous page. Feed back as `cursor`. */
export type ExceptionsCursor = QueryDocumentSnapshot<DocumentData> | null;

export interface ExceptionsFilter {
  /** Required status tab — the only ever-present equality predicate. */
  status: ExceptionStatus;
  /** Optional single non-status equality predicate (index limit — see file header). */
  exceptionType?: ExceptionType | null;
}

export interface ExceptionsPageResult {
  data: ExceptionRow[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
  /** Cursor for the NEXT page. */
  cursor: ExceptionsCursor;
  hasMore: boolean;
}

/**
 * Build the (index-backed) where/orderBy constraints for one exceptions query.
 *
 * - No type filter → `where(status ==) · orderBy(severityRank DESC) · orderBy(createdAt DESC)`,
 *   served by the `operationalExceptions(status, severityRank DESC, createdAt DESC)` index:
 *   most-severe first, then newest. `severityRank` is the NUMERIC sort key (specs/04 §4.10);
 *   the `severity` string does not sort by importance and is never ordered on.
 * - Type filter → `where(status ==) · where(exceptionType ==) · orderBy(createdAt DESC)`,
 *   served by the `operationalExceptions(exceptionType, status, createdAt DESC)` index. That
 *   index cannot order by severityRank, so a type-filtered view is recency-ordered.
 *
 * Only one non-status equality predicate (exceptionType) is ever applied — no committed
 * composite index admits two equalities alongside the severity ordering.
 */
function buildExceptionConstraints(filter: ExceptionsFilter): QueryConstraint[] {
  const parts: QueryConstraint[] = [where("status", "==", filter.status)];
  if (filter.exceptionType) {
    parts.push(where("exceptionType", "==", filter.exceptionType));
    parts.push(orderBy("createdAt", "desc"));
  } else {
    parts.push(orderBy("severityRank", "desc"));
    parts.push(orderBy("createdAt", "desc"));
  }
  return parts;
}

/**
 * One bounded, cursor-paginated, index-backed page of `operationalExceptions` for the
 * given status tab (+ optional single exceptionType filter). Live: the page re-delivers
 * a row the instant a command mutates its source doc, so a resolved/dismissed row leaves
 * the OPEN tab and appears under its new status on the next snapshot (specs/05 §5.7).
 */
export function useExceptionsPage(
  filter: ExceptionsFilter,
  cursor: ExceptionsCursor = null,
  pageSize?: number,
): ExceptionsPageResult {
  const { status, exceptionType } = filter;

  // useCollectionPage re-subscribes on constraints-array identity, so memoize on the
  // primitive filter fields (never the caller's object identity).
  const constraints = useMemo(
    () => buildExceptionConstraints({ status, exceptionType: exceptionType ?? null }),
    [status, exceptionType],
  );

  const page: CollectionPageState<ExceptionRow> = useCollectionPage<ExceptionRow>(
    OPERATIONAL_EXCEPTIONS,
    { constraints, pageSize, cursor },
  );

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
// Timestamp helpers (a Firestore instant is a Timestamp live, an ISO string on some
// read paths — specs/04). Pure; used by the workbench "age" column.
// ---------------------------------------------------------------------------

function timestampToMillis(ts: FirestoreTimestamp | null | undefined): number | null {
  if (ts == null) return null;
  if (typeof ts === "string") {
    const parsed = Date.parse(ts);
    return Number.isNaN(parsed) ? null : parsed;
  }
  const maybe = ts as { toMillis?: () => number; seconds?: number };
  if (typeof maybe.toMillis === "function") return maybe.toMillis();
  if (typeof maybe.seconds === "number") return maybe.seconds * 1000;
  return null;
}

/** Compact "age" label since `ts` (e.g. "just now", "6m", "4h", "12d"), or "—". `now`
 *  is injectable for testing; defaults to the wall clock. */
export function formatRelativeAge(
  ts: FirestoreTimestamp | null | undefined,
  now: number = Date.now(),
): string {
  const ms = timestampToMillis(ts);
  if (ms == null) return "—";
  const seconds = Math.floor(Math.max(0, now - ms) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

/** Absolute instant for a hover title, in SYSTEM_TIMEZONE (never UTC — specs/README). */
export function formatTimestamp(ts: FirestoreTimestamp | null | undefined): string {
  const ms = timestampToMillis(ts);
  if (ms == null) return "";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: SYSTEM_TIMEZONE,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(ms));
}
