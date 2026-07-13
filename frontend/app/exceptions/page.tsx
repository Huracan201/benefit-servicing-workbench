"use client";

// C3 — Exception workbench (specs/06 §6.4 · specs/15 §15.3 · specs/13). A status-tabbed,
// severity-ranked worklist over the `operationalExceptions` SOURCE collection, with the
// full operator command set per row.
//
// CQRS (specs/02 P7): this screen only READS — through the sanctioned, index-backed,
// cursor-paginated `useExceptionsPage` subscription on the authoritative source docs, so
// a command's effect (a status flip, a new assignee) is reflected the instant it lands;
// it never reads a projection to confirm a write (specs/05 §5.7). Every WRITE goes
// through a Django command via `useCommand` in the per-row action cluster.
//
// The default OPEN queue orders most-severe-first then newest, keyed on the NUMERIC
// severityRank (specs/04 §4.10). A single exceptionType filter is the one non-status
// equality predicate the composite indexes admit; while it is active the queue is
// recency-ordered (that index cannot also sort by severity — see lib/readExceptions).

import { useCallback, useEffect, useMemo, useState } from "react";
import { Tabs, type TabItem } from "@/components/Tabs";
import { Table } from "@/components/Table";
import { Pagination } from "@/components/Pagination";
import { FilterBar, FilterSelect, type FilterOption } from "@/components/FilterBar";
import { humanize, statusMeta } from "@/components/statusMeta";
import { exceptionColumns } from "@/components/exceptions/columns";
import {
  useExceptionsPage,
  type ExceptionRow,
  type ExceptionsCursor,
} from "@/lib/readExceptions";
import { useSession } from "@/lib/session";
import {
  EXCEPTION_STATUSES,
  EXCEPTION_TYPES,
  type ExceptionStatus,
  type ExceptionType,
} from "@/lib/types";

const PAGE_SIZE = 25;

const TYPE_OPTIONS: FilterOption[] = EXCEPTION_TYPES.map((t) => ({
  value: t,
  label: humanize(t),
}));

interface PageState {
  /** Filter signature — a change resets the cursor stack to page one. */
  key: string;
  /** Start cursor per visited page; index 0 is always null (first page). */
  cursors: ExceptionsCursor[];
  index: number;
}

function ErrorSurface({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded border border-critical/[0.4] bg-critical/[0.08] px-4 py-10 text-center"
    >
      <p className="font-display text-sm font-semibold text-critical">
        Couldn&rsquo;t load exceptions
      </p>
      <p className="mx-auto mt-1 max-w-md text-xs text-ink-2">{message}</p>
      <p className="mx-auto mt-2 max-w-md text-xs text-ink-3">
        This usually means a query hit an unsupported filter combination (a missing
        composite index). Adjust the filters and try again.
      </p>
    </div>
  );
}

export default function ExceptionsPage() {
  const { role, user } = useSession();
  const currentUid = user?.uid ?? null;

  const [status, setStatus] = useState<ExceptionStatus>("OPEN");
  const [exceptionType, setExceptionType] = useState<ExceptionType | null>(null);

  const filterKey = `${status}|${exceptionType ?? ""}`;

  // Cursor-stack pagination (mirrors the portfolio list). Keying the stack by the filter
  // signature resets to page one on any tab/filter change; recomputing `active` from the
  // current key each render guarantees a filter change never reuses a stale cursor.
  const [pageState, setPageState] = useState<PageState>({
    key: filterKey,
    cursors: [null],
    index: 0,
  });
  const active: PageState =
    pageState.key === filterKey
      ? pageState
      : { key: filterKey, cursors: [null], index: 0 };

  useEffect(() => {
    if (pageState.key !== filterKey) {
      setPageState({ key: filterKey, cursors: [null], index: 0 });
    }
  }, [filterKey, pageState.key]);

  const cursor = active.cursors[active.index] ?? null;
  const result = useExceptionsPage({ status, exceptionType }, cursor, PAGE_SIZE);

  const columns = useMemo(
    () => exceptionColumns({ role, currentUid }),
    [role, currentUid],
  );

  const tabs = useMemo<TabItem[]>(
    () => EXCEPTION_STATUSES.map((s) => ({ key: s, label: statusMeta(s).label })),
    [],
  );

  const goNext = useCallback(() => {
    if (!result.hasMore) return;
    const next = result.cursor;
    setPageState((ps) => {
      const cur =
        ps.key === filterKey ? ps : { key: filterKey, cursors: [null], index: 0 };
      const base = cur.cursors.slice(0, cur.index + 1);
      return { key: filterKey, cursors: [...base, next], index: cur.index + 1 };
    });
  }, [result.hasMore, result.cursor, filterKey]);

  const goPrev = useCallback(() => {
    setPageState((ps) => {
      const cur =
        ps.key === filterKey ? ps : { key: filterKey, cursors: [null], index: 0 };
      return { ...cur, index: Math.max(0, cur.index - 1) };
    });
  }, [filterKey]);

  const onTabChange = useCallback((key: string) => {
    setStatus(key as ExceptionStatus);
  }, []);

  const onTypeChange = useCallback((value: string) => {
    setExceptionType(value === "" ? null : (value as ExceptionType));
  }, []);

  const shown = result.data.length;
  const pageNumber = active.index + 1;
  const statusLabel = statusMeta(status).label.toLowerCase();
  const sortNote = exceptionType
    ? "newest first"
    : "most-severe first, then newest";
  const countText = result.loading
    ? "Loading exceptions…"
    : `${shown} ${statusLabel} exception${shown === 1 ? "" : "s"} on this page · ${sortNote}`;

  return (
    <div className="space-y-4">
      <header className="min-w-0">
        <h1 className="font-display text-h1 font-semibold text-ink">Exception workbench</h1>
        <p className="mt-0.5 text-sm text-ink-2">{countText}</p>
      </header>

      <Tabs
        tabs={tabs}
        active={status}
        onChange={onTabChange}
        ariaLabel="Exception status"
      />

      <FilterBar>
        <FilterSelect
          label="Type"
          value={exceptionType ?? ""}
          options={TYPE_OPTIONS}
          onChange={onTypeChange}
          allLabel="All types"
        />
      </FilterBar>

      {result.error ? (
        <ErrorSurface message={result.error.message} />
      ) : (
        <>
          <Table<ExceptionRow>
            caption="Operational exceptions"
            columns={columns}
            rows={result.data}
            rowKey={(r) => r.id}
            loading={result.loading}
            skeletonRows={10}
            emptyMessage={`No ${statusLabel} exceptions match these filters`}
          />
          <Pagination
            onPrev={goPrev}
            onNext={goNext}
            hasPrev={active.index > 0}
            hasNext={result.hasMore}
            loading={result.loading}
            label={`${shown} shown · page ${pageNumber}`}
          />
        </>
      )}
    </div>
  );
}
