"use client";

// Typed read-model access layer (specs/05). The web client is READ-ONLY against
// Firestore read models; every write goes through the Django command API (CQRS —
// specs/02 P7). Screens consume ONLY the hooks below — they never build raw
// Firestore queries and never read a projection to make a financial DECISION
// (display only; aggregates are eventually consistent — specs/05 §5.7).
//
// This module is the single import surface for read-model data:
//   - the doc interfaces (re-exported from lib/types — the ONE definition, kept in
//     lockstep with backend/core/schema.py; re-exported here so screens import the
//     data contract from `@/lib/readModels`);
//   - the collection-path + doc-id constants;
//   - the SYSTEM_TIMEZONE period helpers (YYYY-MM in America/New_York, never UTC);
//   - thin, typed hooks wrapping the generic useDocument / useCollectionPage hooks,
//     which enforce the specs/05 §5.6 subscription rules (bounded limit + indexed
//     predicate + cursor pagination).

import {
  type DocumentData,
  type QueryConstraint,
  type QueryDocumentSnapshot,
  orderBy,
  where,
} from "firebase/firestore";
import { useMemo } from "react";
import { formatCents } from "@/lib/format";
import type {
  BenefitStatus,
  EmployerSummary,
  EmploymentStatus,
  LoanStatus,
  LoanWorkbench,
  PortfolioSummaryCurrent,
  PortfolioSummaryPeriod,
  ServicingEvent,
} from "@/lib/types";
import {
  type CollectionPageState,
  useCollectionPage,
} from "@/hooks/useCollectionPage";
import { type DocumentState, useDocument } from "@/hooks/useDocument";

// ---------------------------------------------------------------------------
// Re-exported data contract (single definition lives in lib/types.ts).
// ---------------------------------------------------------------------------

export type {
  PortfolioSummaryCurrent,
  PortfolioSummaryPeriod,
  EmployerSummary,
  EmployerSummaryPeriod,
  LoanWorkbench,
  ServicingEvent,
  ServicingEventType,
} from "@/lib/types";

/** Integer US cents (specs/README) — the money unit for every `*Cents` field.
 *  Format at the render boundary with {@link formatCents}; never do float math. */
export type Cents = number;

/** Re-exported so screens import money + data from one module. Integer cents in,
 *  "$x,xxx.xx" string out (specs/15 §15.1). */
export { formatCents };

/** A read-model doc plus its Firestore document id (the generic hooks spread
 *  `{ id, ...data }`). For docId-keyed read models the id equals the natural key
 *  (loanId / employerId / periodLabel); for servicingEvents it is the eventId. */
export type WithId<T> = T & { id: string };

// ---------------------------------------------------------------------------
// Collection paths & doc ids (the only place these strings live on the client).
// Must match firebase/firestore.rules read-allowed collections + specs/05.
// ---------------------------------------------------------------------------

export const PORTFOLIO_SUMMARIES = "portfolioSummaries" as const;
export const EMPLOYER_SUMMARIES = "employerSummaries" as const;
export const LOAN_WORKBENCHES = "loanWorkbenches" as const;
export const SERVICING_EVENTS = "servicingEvents" as const;

/** The point-in-time portfolio summary doc id (specs/05 §5.3). */
export const PORTFOLIO_CURRENT_DOC_ID = "current" as const;

// ---------------------------------------------------------------------------
// SYSTEM_TIMEZONE period helpers (specs/README — periods derive from
// America/New_York, NEVER UTC). The period doc id IS the YYYY-MM label.
// ---------------------------------------------------------------------------

/** The calendar timezone all periods derive from (specs/README). Overridable via
 *  env to stay in lockstep with the backend's SYSTEM_TIMEZONE. */
export const SYSTEM_TIMEZONE =
  process.env.NEXT_PUBLIC_SYSTEM_TIMEZONE ?? "America/New_York";

/** The `YYYY-MM` period label for `date` in SYSTEM_TIMEZONE (not UTC). Uses
 *  Intl parts so the month never flips across a UTC/DST boundary (specs/05 §5.3). */
export function periodLabelFor(date: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: SYSTEM_TIMEZONE,
    year: "numeric",
    month: "2-digit",
  }).formatToParts(date);
  const year = parts.find((p) => p.type === "year")?.value ?? "0000";
  const month = parts.find((p) => p.type === "month")?.value ?? "00";
  return `${year}-${month}`;
}

/** The current `YYYY-MM` period label in SYSTEM_TIMEZONE (the current-period doc
 *  id the dashboard subscribes to — specs/05 §5.6). */
export function currentPeriodLabel(): string {
  return periodLabelFor();
}

// ---------------------------------------------------------------------------
// Hook return shapes.
// ---------------------------------------------------------------------------

/** Single-doc read-model state ({ data, loading, error } + `empty`). */
export type ReadModelDoc<T> = DocumentState<T>;

/** List read-model state — `data` is the current page's items. */
export interface ReadModelList<T> {
  data: T[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
}

/** Opaque pagination cursor for {@link useLoanWorkbenchesPage}: the last document
 *  of the previous page. Pass it back as the `cursor` arg to load the next page. */
export type LoanWorkbenchCursor = QueryDocumentSnapshot<DocumentData> | null;

/** Paged list state — adds `cursor` (feed to next call) + `hasMore`. */
export interface ReadModelPage<T> extends ReadModelList<T> {
  cursor: LoanWorkbenchCursor;
  hasMore: boolean;
}

function toList<T>(page: CollectionPageState<T>): ReadModelList<T> {
  return {
    data: page.items,
    loading: page.loading,
    error: page.error,
    empty: page.empty,
  };
}

// ---------------------------------------------------------------------------
// Portfolio dashboard (2-doc subscription — specs/05 §5.6).
// ---------------------------------------------------------------------------

/** `portfolioSummaries/current` — point-in-time portfolio totals (specs/05 §5.3).
 *  Eventually consistent: an aggregate may lag a just-completed payment. */
export function usePortfolioCurrent(): ReadModelDoc<PortfolioSummaryCurrent> {
  return useDocument<PortfolioSummaryCurrent>(
    PORTFOLIO_SUMMARIES,
    PORTFOLIO_CURRENT_DOC_ID,
  );
}

/** `portfolioSummaries/{YYYY-MM}` — per-period flow metrics (specs/05 §5.3).
 *  Defaults to the current period in SYSTEM_TIMEZONE. Pass a label to page back. */
export function usePortfolioPeriod(
  periodLabel?: string,
): ReadModelDoc<PortfolioSummaryPeriod> {
  const label = periodLabel ?? currentPeriodLabel();
  return useDocument<PortfolioSummaryPeriod>(PORTFOLIO_SUMMARIES, label);
}

// ---------------------------------------------------------------------------
// Employer summaries.
// ---------------------------------------------------------------------------

/** Ordering preset for {@link useEmployerSummaries}. */
const EMPLOYER_SORT: QueryConstraint[] = [orderBy("employerName", "asc")];
/** Employer count is small; one bounded page covers the roster (specs/05 §5.6). */
const EMPLOYER_PAGE_SIZE = 200;

/** `employerSummaries/{employerId}` — point-in-time per employer (specs/05 §5.4),
 *  ordered by name. Bounded page; the MVP roster fits in one page. */
export function useEmployerSummaries(): ReadModelList<WithId<EmployerSummary>> {
  const page = useCollectionPage<WithId<EmployerSummary>>(EMPLOYER_SUMMARIES, {
    constraints: EMPLOYER_SORT,
    pageSize: EMPLOYER_PAGE_SIZE,
  });
  return toList(page);
}

// ---------------------------------------------------------------------------
// Loan workbench table — the portfolio grid (specs/05 §5.5).
// ---------------------------------------------------------------------------

/** Ordering presets — each maps to a composite index in
 *  firebase/firestore.indexes.json (specs/13). See {@link LoanWorkbenchFilters}. */
export type LoanWorkbenchSort =
  /** By document id (implicit __name__) — valid with any equality-only filter set. */
  | "default"
  /** orderBy nextContributionDate ASC — REQUIRES the `benefitStatus` filter
   *  (index: benefitStatus, nextContributionDate). */
  | "nextContribution"
  /** where openExceptionCount > 0, then orderBy openExceptionCount DESC, updatedAt
   *  DESC — returns ONLY loans with an open exception; use with NO equality filters
   *  (index: openExceptionCount, updatedAt). */
  | "openExceptions";

/**
 * Filters for {@link useLoanWorkbenchesPage}. Every combination below is
 * index-backed (firebase/firestore.indexes.json); other combinations will be
 * rejected by Firestore at query time. Valid shapes:
 *   - { employerId, benefitStatus?, loanStatus? }           sort: "default"
 *   - { employerId, loanStatus? }                           sort: "default"
 *   - { employmentStatus, loanStatus? }                     sort: "default"
 *   - { benefitStatus }                                     sort: "nextContribution"
 *   - {}                                                    sort: "openExceptions"
 */
export interface LoanWorkbenchFilters {
  employerId?: string;
  benefitStatus?: BenefitStatus;
  loanStatus?: LoanStatus;
  employmentStatus?: EmploymentStatus;
  sort?: LoanWorkbenchSort;
}

/** Build the (index-backed) where/orderBy constraints for a loan-workbench query. */
function buildLoanWorkbenchConstraints(f: LoanWorkbenchFilters): QueryConstraint[] {
  const parts: QueryConstraint[] = [];
  if (f.employerId) parts.push(where("employerId", "==", f.employerId));
  if (f.benefitStatus) parts.push(where("benefitStatus", "==", f.benefitStatus));
  if (f.loanStatus) parts.push(where("loanStatus", "==", f.loanStatus));
  if (f.employmentStatus)
    parts.push(where("employmentStatus", "==", f.employmentStatus));

  switch (f.sort ?? "default") {
    case "nextContribution":
      parts.push(orderBy("nextContributionDate", "asc"));
      break;
    case "openExceptions":
      // Restrict to loans that actually HAVE an open exception (not the whole
      // portfolio re-sorted). Firestore allows an inequality + orderBy on the SAME
      // leading field; the (openExceptionCount DESC, updatedAt DESC) composite index
      // already serves this — no new index required.
      parts.push(where("openExceptionCount", ">", 0));
      parts.push(orderBy("openExceptionCount", "desc"));
      parts.push(orderBy("updatedAt", "desc"));
      break;
    // "default": implicit __name__ ordering — valid with any equality prefix and
    // stable for cursor pagination.
  }
  return parts;
}

/**
 * One bounded, cursor-paginated page of `loanWorkbenches` (specs/05 §5.5, §5.6).
 * `filters` selects an index-backed where/orderBy set (see {@link LoanWorkbenchFilters});
 * `cursor` is the `cursor` from the previous call's result; `limit` clamps the page.
 * Returns the page items plus `{ cursor, hasMore }` for "load more".
 */
export function useLoanWorkbenchesPage(
  filters: LoanWorkbenchFilters = {},
  cursor: LoanWorkbenchCursor = null,
  limit?: number,
): ReadModelPage<WithId<LoanWorkbench>> {
  const { employerId, benefitStatus, loanStatus, employmentStatus, sort } = filters;

  // useCollectionPage re-subscribes on constraints-array identity, so memoize on
  // the primitive filter fields (not the caller's object identity).
  const constraints = useMemo(
    () =>
      buildLoanWorkbenchConstraints({
        employerId,
        benefitStatus,
        loanStatus,
        employmentStatus,
        sort,
      }),
    [employerId, benefitStatus, loanStatus, employmentStatus, sort],
  );

  const page = useCollectionPage<WithId<LoanWorkbench>>(LOAN_WORKBENCHES, {
    constraints,
    pageSize: limit,
    cursor,
  });

  return {
    ...toList(page),
    cursor: page.lastVisible,
    hasMore: page.hasMore,
  };
}

// ---------------------------------------------------------------------------
// Global servicing-event stream (specs/04 §4.9, specs/05 §5.6).
// ---------------------------------------------------------------------------

/** The bounded live tail of the global `servicingEvents` audit stream, newest
 *  first. Always limited (specs/05 §5.6); backed by the automatic single-field
 *  index on `createdAt`. For a per-loan feed, subscribe to that loan's events
 *  subcollection instead of this global stream. */
export function useRecentServicingEvents(
  limit = 25,
): ReadModelList<WithId<ServicingEvent>> {
  const constraints = useMemo(() => [orderBy("createdAt", "desc")], []);
  const page = useCollectionPage<WithId<ServicingEvent>>(SERVICING_EVENTS, {
    constraints,
    pageSize: limit,
  });
  return toList(page);
}

// Re-exported for screens that need the raw timestamp union at a prop boundary.
export type { FirestoreTimestamp } from "@/lib/types";
