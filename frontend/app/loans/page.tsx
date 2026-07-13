"use client";

// U6 — Loan portfolio (specs/05 §5.5, §5.6 · specs/13 · specs/15 §15.3). The filterable,
// paginated master list — the operator's entry point into an account.
//
// CQRS (specs/02 P7): this screen only READS the `loanWorkbenches` read model through
// the sanctioned subscription hook; it never reads through Django and never reads a
// projection to make a financial DECISION — the rows are display only, and the exception
// count is eventually consistent (specs/05 §5.7). Every write happens on the account
// screen through a Django command.
//
// The two load-bearing disciplines live in their own modules:
//   • useLoanFilters   — keeps the query on an index-backed shape (specs/13 §13.2a);
//   • useLoanWorkbenchesPage(filters, cursor, 25) — bounded, indexed, cursor-paginated.
// Money is integer cents until formatCents at the render boundary (LOAN_COLUMNS).

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Table } from "@/components/Table";
import { Pagination } from "@/components/Pagination";
import { PortfolioFilters } from "@/components/loans/PortfolioFilters";
import { useLoanFilters } from "@/components/loans/useLoanFilters";
import { LOAN_COLUMNS, type LoanRow } from "@/components/loans/columns";
import {
  useEmployerSummaries,
  useLoanWorkbenchesPage,
  type LoanWorkbenchCursor,
} from "@/lib/readModels";
import type { FilterOption } from "@/components/FilterBar";

const PAGE_SIZE = 25;

interface PageState {
  key: string;
  /** Start cursor for each visited page; index 0 is always null (first page). */
  cursors: LoanWorkbenchCursor[];
  index: number;
}

function ErrorSurface({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded border border-critical/[0.4] bg-critical/[0.08] px-4 py-10 text-center"
    >
      <p className="font-display text-sm font-semibold text-critical">
        Couldn&rsquo;t load the loan portfolio
      </p>
      <p className="mx-auto mt-1 max-w-md text-xs text-ink-2">{message}</p>
      <p className="mx-auto mt-2 max-w-md text-xs text-ink-3">
        This usually means a query hit an unsupported filter combination (a missing
        composite index). Adjust the filters and try again.
      </p>
    </div>
  );
}

export default function LoansPage() {
  const router = useRouter();
  const filters = useLoanFilters();
  const employers = useEmployerSummaries();

  const employerOptions = useMemo<FilterOption[]>(
    () => employers.data.map((e) => ({ value: e.employerId, label: e.employerName })),
    [employers.data],
  );

  // Cursor-stack pagination. Keying the stack by the filter signature resets it to page
  // one whenever the query changes — and, because we recompute `active` from the current
  // key each render, a filter change never passes a stale cursor into the new query.
  const [pageState, setPageState] = useState<PageState>({
    key: filters.key,
    cursors: [null],
    index: 0,
  });
  const active: PageState =
    pageState.key === filters.key ? pageState : { key: filters.key, cursors: [null], index: 0 };

  useEffect(() => {
    if (pageState.key !== filters.key) {
      setPageState({ key: filters.key, cursors: [null], index: 0 });
    }
  }, [filters.key, pageState.key]);

  const cursor = active.cursors[active.index] ?? null;
  const result = useLoanWorkbenchesPage(filters.resolved, cursor, PAGE_SIZE);

  const goNext = useCallback(() => {
    if (!result.hasMore) return;
    const next = result.cursor;
    setPageState((ps) => {
      const cur = ps.key === filters.key ? ps : { key: filters.key, cursors: [null], index: 0 };
      const base = cur.cursors.slice(0, cur.index + 1);
      return { key: filters.key, cursors: [...base, next], index: cur.index + 1 };
    });
  }, [result.hasMore, result.cursor, filters.key]);

  const goPrev = useCallback(() => {
    setPageState((ps) => {
      const cur = ps.key === filters.key ? ps : { key: filters.key, cursors: [null], index: 0 };
      return { ...cur, index: Math.max(0, cur.index - 1) };
    });
  }, [filters.key]);

  const openAccount = useCallback(
    (row: LoanRow) => router.push(`/loans/${row.loanId}`),
    [router],
  );

  const shown = result.data.length;
  const pageNumber = active.index + 1;
  const paged = result.hasMore || active.index > 0;
  const countText = result.loading
    ? "Loading loans…"
    : `${shown} loan${shown === 1 ? "" : "s"}${paged ? " on this page" : ""}`;

  return (
    <div className="space-y-4">
      <header className="min-w-0">
        <h1 className="font-display text-h1 font-semibold text-ink">Loan portfolio</h1>
        <p className="mt-0.5 text-sm text-ink-2">{countText} · click a row to open the account</p>
      </header>

      <PortfolioFilters
        controls={filters}
        employerOptions={employerOptions}
        employersLoading={employers.loading}
      />

      {result.error ? (
        <ErrorSurface message={result.error.message} />
      ) : (
        <>
          <Table<LoanRow>
            caption="Loan portfolio"
            columns={LOAN_COLUMNS}
            rows={result.data}
            rowKey={(r) => r.loanId}
            loading={result.loading}
            skeletonRows={10}
            onRowClick={openAccount}
            emptyMessage="No loans match these filters"
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
