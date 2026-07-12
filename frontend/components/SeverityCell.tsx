// SeverityCell — a 3px colored rail keyed by numeric severityRank (specs/04 §4.10:
// rank sorts by importance; the `severity` string does not) plus a text label, so the
// severity reads without relying on color. Used in exception worklists and detail rows.

import type { ReactNode } from "react";
import { severityMeta, solidBg } from "@/components/statusMeta";

export interface SeverityCellProps {
  /** Numeric severity rank (10 LOW · 20 MEDIUM · 30 HIGH · 40 CRITICAL). */
  severityRank: number;
  /** Primary line (e.g. exception summary). */
  title: ReactNode;
  /** Optional secondary line (e.g. entity ref + failure code). */
  subtitle?: ReactNode;
  /** Override the visible severity label (defaults to the mapped label). */
  label?: ReactNode;
  className?: string;
}

export function SeverityCell({
  severityRank,
  title,
  subtitle,
  label,
  className,
}: SeverityCellProps) {
  const meta = severityMeta(severityRank);
  return (
    <div
      className={[
        "grid grid-cols-[3px_1fr_auto] items-center gap-3",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span
        aria-hidden="true"
        className={`h-5 w-[3px] shrink-0 rounded-sm ${solidBg(meta.token)}`}
      />
      <span className="min-w-0">
        <span className="block truncate font-medium text-ink">{title}</span>
        {subtitle ? (
          <span className="block truncate text-xs text-ink-3">{subtitle}</span>
        ) : null}
      </span>
      <span className="shrink-0 text-xs font-semibold text-ink-2">
        {label ?? meta.label}
      </span>
    </div>
  );
}

export default SeverityCell;
