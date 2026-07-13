"use client";

// U-C2 — Payment operations queue (specs/06 §6.1 · specs/09 · specs/13 · specs/15 §15.3). A
// status-tabbed, live, cursor-paginated worklist over the `scheduledContributions` SOURCE
// collection, with per-row process / retry commands.
//
// CQRS (specs/02 P7): this screen READS the authoritative source collection directly (never a
// projection — specs/05 §5.7) so the queue reflects the state a command transactionally landed;
// every WRITE goes through the Django command client via the shared useCommand engine (the row
// actions). A processed / retried contribution transitions status and therefore leaves its tab on
// its own the moment the source doc updates — no manual refetch.
//
// The two disciplines live in their own modules: useContributionsPage (bounded, index-backed,
// cursor-paginated — specs/05 §5.6) and the payments columns (money via formatCents at the render
// boundary; dates in SYSTEM_TIMEZONE). Tabs mirror the contribution state machine exactly.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Tabs, type TabItem } from "@/components/Tabs";
import { Table } from "@/components/Table";
import { Pagination } from "@/components/Pagination";
import { statusMeta } from "@/components/statusMeta";
import {
  BASE_PAYMENT_COLUMNS,
  actionFor,
  actionsColumn,
} from "@/components/payments/columns";
import {
  useContributionsPage,
  type ContributionCursor,
  type ContributionRow,
} from "@/lib/readContributions";
import { CONTRIBUTION_STATUSES, type ContributionStatus } from "@/lib/types";

/** First tab: the operator's primary queue of installments awaiting processing. */
const DEFAULT_STATUS: ContributionStatus = "SCHEDULED";

interface PageState {
  /** The tab this cursor stack belongs to; a tab change resets pagination to page one. */
  key: ContributionStatus;
  /** Start cursor for each visited page; index 0 is always null (first page). */
  cursors: ContributionCursor[];
  index: number;
}

function ErrorSurface({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded border border-critical/[0.4] bg-critical/[0.08] px-4 py-10 text-center"
    >
      <p className="font-display text-sm font-semibold text-critical">
        Couldn&rsquo;t load the payment queue
      </p>
      <p className="mx-auto mt-1 max-w-md text-xs text-ink-2">{message}</p>
      <p className="mx-auto mt-2 max-w-md text-xs text-ink-3">
        This usually means the browser couldn&rsquo;t reach Firestore or the session
        isn&rsquo;t authorized. Check your connection and try again.
      </p>
    </div>
  );
}

export default function PaymentsPage() {
  const router = useRouter();
  const [active, setActive] = useState<ContributionStatus>(DEFAULT_STATUS);

  // One tab per contribution status (specs/06 §6.1), labeled via the shared status map so the
  // active tab is never communicated by color alone.
  const tabs = useMemo<TabItem[]>(
    () => CONTRIBUTION_STATUSES.map((s) => ({ key: s, label: statusMeta(s).label })),
    [],
  );

  // Cursor-stack pagination, keyed by the active tab. Recomputing `current` from the active key
  // each render means a tab change never feeds a stale cursor into the new query.
  const [pageState, setPageState] = useState<PageState>({
    key: DEFAULT_STATUS,
    cursors: [null],
    index: 0,
  });
  const current: PageState =
    pageState.key === active ? pageState : { key: active, cursors: [null], index: 0 };

  useEffect(() => {
    if (pageState.key !== active) {
      setPageState({ key: active, cursors: [null], index: 0 });
    }
  }, [active, pageState.key]);

  const cursor = current.cursors[current.index] ?? null;
  const result = useContributionsPage(active, cursor);

  // Append the process / retry column only on actionable tabs (SCHEDULED / FAILED / RETRY_PENDING).
  const action = actionFor(active);
  const columns = useMemo(
    () => (action ? [...BASE_PAYMENT_COLUMNS, actionsColumn(action)] : BASE_PAYMENT_COLUMNS),
    [action],
  );

  const goNext = useCallback(() => {
    if (!result.hasMore) return;
    const next = result.cursor;
    setPageState((ps) => {
      const cur = ps.key === active ? ps : { key: active, cursors: [null], index: 0 };
      const base = cur.cursors.slice(0, cur.index + 1);
      return { key: active, cursors: [...base, next], index: cur.index + 1 };
    });
  }, [result.hasMore, result.cursor, active]);

  const goPrev = useCallback(() => {
    setPageState((ps) => {
      const cur = ps.key === active ? ps : { key: active, cursors: [null], index: 0 };
      return { ...cur, index: Math.max(0, cur.index - 1) };
    });
  }, [active]);

  const openAccount = useCallback(
    (row: ContributionRow) => router.push(`/loans/${row.loanId}`),
    [router],
  );

  const label = statusMeta(active).label.toLowerCase();
  const shown = result.data.length;
  const pageNumber = current.index + 1;
  const paged = result.hasMore || current.index > 0;
  const countText = result.loading
    ? "Loading contributions…"
    : `${shown} ${label} contribution${shown === 1 ? "" : "s"}${paged ? " on this page" : ""}`;

  return (
    <div className="space-y-4">
      <header className="min-w-0">
        <h1 className="font-display text-h1 font-semibold text-ink">Payment operations</h1>
        <p className="mt-0.5 text-sm text-ink-2">{countText} · click a row to open the account</p>
      </header>

      <Tabs
        tabs={tabs}
        active={active}
        onChange={(k) => setActive(k as ContributionStatus)}
        ariaLabel="Contribution status"
      />

      {result.error ? (
        <ErrorSurface message={result.error.message} />
      ) : (
        <>
          <Table<ContributionRow>
            caption={`${statusMeta(active).label} contributions`}
            columns={columns}
            rows={result.data}
            rowKey={(r) => r.id}
            loading={result.loading}
            skeletonRows={10}
            onRowClick={openAccount}
            emptyMessage={`No ${label} contributions`}
          />
          <Pagination
            onPrev={goPrev}
            onNext={goNext}
            hasPrev={current.index > 0}
            hasNext={result.hasMore}
            loading={result.loading}
            label={`${shown} shown · page ${pageNumber}`}
          />
        </>
      )}
    </div>
  );
}
