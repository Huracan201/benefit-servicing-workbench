// DenseTable (exported as both `Table` and `DenseTable`) — the primary data idiom
// (specs/15 §15.1, U1 design). Sticky uppercase micro head; tabular-nums, mono,
// right-aligned money; optional clickable rows (mouse convenience; keyboard/AT via an
// in-cell link); an optional
// 3px severity stripe per row keyed by a color token; and loading-skeleton + empty
// states. The Column<T> shape and TableProps<T> are backward-compatible with the
// Phase-1 scaffold — the new behaviors are all opt-in.

import type { ReactNode } from "react";
import Skeleton from "@/components/Skeleton";
import { solidBg, type ColorToken } from "@/components/statusMeta";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Cell renderer for a row. */
  render: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  /** Apply tabular-nums + mono (for money / counts). */
  numeric?: boolean;
  /** Optional fixed column width (CSS length, e.g. "10rem"). */
  width?: string;
}

export interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Accessible caption (visually hidden). */
  caption?: string;
  loading?: boolean;
  /** How many skeleton rows to show while loading. */
  skeletonRows?: number;
  emptyMessage?: ReactNode;
  /**
   * Mouse convenience: makes rows clickable (adds cursor + hover + onClick). The
   * `<tr>` deliberately keeps native row/cell semantics — it gets NO role/tabIndex —
   * so keyboard and assistive-tech users navigate via a real <a>/<button> rendered
   * inside the row's primary cell (screens provide that in later slices).
   */
  onRowClick?: (row: T) => void;
  /** Return a color token to paint a 3px left severity stripe for the row. */
  rowStripe?: (row: T) => ColorToken | undefined;
}

const alignClass = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
} as const;

export function Table<T>({
  columns,
  rows,
  rowKey,
  caption,
  loading = false,
  skeletonRows = 5,
  emptyMessage = "No records.",
  onRowClick,
  rowStripe,
}: TableProps<T>) {
  const striped = typeof rowStripe === "function";
  // The stripe occupies its own leading column; account for it in colSpans.
  const totalCols = columns.length + (striped ? 1 : 0);

  return (
    <div className="overflow-x-auto rounded border border-border bg-surface">
      {loading ? (
        <span role="status" className="sr-only">
          Loading {caption ?? "table"}
        </span>
      ) : null}
      <table aria-busy={loading || undefined} className="w-full border-collapse text-body">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-border bg-surface-2">
            {striped ? (
              <th scope="col" className="w-[3px] p-0">
                <span className="sr-only">Severity</span>
              </th>
            ) : null}
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={col.width ? { width: col.width } : undefined}
                className={[
                  "sticky top-0 z-[1] bg-surface-2 px-3 py-2.5 text-micro font-semibold uppercase tracking-wide text-ink-3",
                  alignClass[col.align ?? "left"],
                ].join(" ")}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: skeletonRows }).map((_, r) => (
              <tr key={`sk-${r}`} className="border-b border-border last:border-0">
                {striped ? <td className="w-[3px] p-0" /> : null}
                {columns.map((col) => (
                  <td key={col.key} className="px-3 py-2.5">
                    <Skeleton
                      className={
                        col.align === "right" ? "ml-auto h-4 w-16" : "h-4 w-24"
                      }
                    />
                  </td>
                ))}
              </tr>
            ))
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={totalCols}
                className="px-3 py-10 text-center text-ink-3"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const clickable = typeof onRowClick === "function";
              const stripeToken = striped ? rowStripe?.(row) : undefined;
              return (
                <tr
                  key={rowKey(row)}
                  className={[
                    "border-b border-border last:border-0",
                    // Mouse-only affordance — the row keeps native row/cell semantics
                    // (no role/tabIndex); keyboard + AT users activate via a real
                    // link/button in the primary cell (see the onRowClick doc).
                    clickable ? "cursor-pointer hover:bg-surface-2" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={clickable ? () => onRowClick?.(row) : undefined}
                >
                  {striped ? (
                    <td className="w-[3px] p-0">
                      <span
                        aria-hidden="true"
                        className={[
                          "block h-full min-h-[2.25rem] w-[3px]",
                          stripeToken ? solidBg(stripeToken) : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                      />
                    </td>
                  ) : null}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={[
                        "px-3 py-2.5 align-middle",
                        alignClass[col.align ?? "left"],
                        col.numeric ? "font-mono tabular-nums" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    >
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Alias — the U1 kit name for the same component. */
export const DenseTable = Table;

export default Table;
