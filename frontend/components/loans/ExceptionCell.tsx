// U6 — the portfolio "Excep." cell: a colored dot + open-exception count, mirroring
// the `exc-dot` idiom in the wireframe. Color is only ONE channel — the count is
// always shown, and an sr-only phrase spells it out, so the cell reads without color
// (specs/15 §15.1).
//
// Contract note: `loanWorkbenches` carries `openExceptionCount` but not the top open
// severityRank, so with today's read model the dot signals *presence* of open
// exceptions ("serious" attention token), not their severity. If/when the read model
// adds a `topOpenExceptionSeverityRank`, pass it as `severityRank` and the dot + label
// switch to the true per-severity color via `severityMeta` — the plumbing is ready.

import { severityMeta, solidBg } from "@/components/statusMeta";

export interface ExceptionCellProps {
  count: number;
  /** Optional top open severityRank (10/20/30/40). Absent in the current read model. */
  severityRank?: number;
}

export function ExceptionCell({ count, severityRank }: ExceptionCellProps) {
  if (!count) {
    return <span className="text-ink-3">0</span>;
  }
  const hasRank = severityRank != null;
  const token = hasRank ? severityMeta(severityRank).token : "serious";
  const severityLabel = hasRank ? severityMeta(severityRank).label : null;
  const plural = count === 1 ? "" : "s";
  return (
    <span
      className="inline-flex items-center justify-end gap-1.5"
      title={`${count} open exception${plural}${severityLabel ? ` · ${severityLabel}` : ""}`}
    >
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${solidBg(token)}`} />
      <span>{count}</span>
      <span className="sr-only">
        {count} open exception{plural}
        {severityLabel ? `, ${severityLabel} severity` : ""}
      </span>
    </span>
  );
}

export default ExceptionCell;
