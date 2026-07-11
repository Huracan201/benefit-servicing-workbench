// Table primitive (specs/15 §15.1: clear tables are the primary idiom). A small,
// generic, keyboard-navigable table with header + rows, an accessible caption, and
// loading / empty states. Money columns should use `tabular-nums` (align="right").

import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Cell renderer for a row. */
  render: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  /** Apply tabular-nums (for money / counts). */
  numeric?: boolean;
}

export interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  caption?: string;
  loading?: boolean;
  emptyMessage?: string;
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
  emptyMessage = "No records.",
}: TableProps<T>) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-border bg-surface-muted">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={`px-3 py-2 font-medium text-content-muted ${alignClass[col.align ?? "left"]}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-8 text-center text-content-muted"
              >
                Loading…
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-8 text-center text-content-muted"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                className="border-b border-border last:border-0 hover:bg-surface-muted/60"
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={[
                      "px-3 py-2",
                      alignClass[col.align ?? "left"],
                      col.numeric ? "tabular-nums" : "",
                    ].join(" ")}
                  >
                    {col.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export default Table;
