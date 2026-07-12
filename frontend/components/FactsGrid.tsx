// FactsGrid — a 2-column label/value description list (U1 design) for entity detail
// panels. Semantic <dl> so each fact is a labeled term/definition pair. Values are
// passed pre-formatted (money from integer cents at the render boundary); set
// `mono` for machine tokens / amounts so digits align.

import type { ReactNode } from "react";

export interface Fact {
  label: ReactNode;
  value: ReactNode;
  /** Render the value in mono tabular (ids, amounts, references). */
  mono?: boolean;
}

export interface FactsGridProps {
  facts: Fact[];
  /** Single-column layout on narrow containers is automatic; force columns here. */
  columns?: 1 | 2;
  className?: string;
}

export function FactsGrid({ facts, columns = 2, className }: FactsGridProps) {
  return (
    <dl
      className={[
        "grid gap-x-6 gap-y-2.5",
        columns === 2 ? "sm:grid-cols-2" : "",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {facts.map((fact, i) => (
        <div
          key={i}
          className="flex items-baseline justify-between gap-4 border-b border-border/60 pb-2 last:border-0"
        >
          <dt className="shrink-0 text-xs uppercase tracking-wide text-ink-3">
            {fact.label}
          </dt>
          <dd
            className={[
              "min-w-0 text-right text-body text-ink",
              fact.mono ? "font-mono tabular-nums" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {fact.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default FactsGrid;
