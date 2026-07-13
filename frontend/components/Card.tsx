// Card — the standard surface panel (U1 design): optional header (title + meta + a
// right-aligned actions slot) over a body. Presentational; pass fully-formatted
// children. Surfaces use the `surface` token + single elevation.

import type { ReactNode } from "react";

export interface CardProps {
  title?: ReactNode;
  /** Sub-line under the title (muted). */
  meta?: ReactNode;
  /** Right-aligned header slot (buttons, filters, a pill). */
  actions?: ReactNode;
  children?: ReactNode;
  /** Remove body padding (e.g. when embedding a full-bleed table). */
  flush?: boolean;
  className?: string;
}

export function Card({ title, meta, actions, children, flush, className }: CardProps) {
  const hasHeader = title != null || meta != null || actions != null;
  return (
    <section
      className={[
        "rounded border border-border bg-surface shadow-elevation",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {hasHeader ? (
        <header className="flex items-start justify-between gap-3 px-4 pt-3.5 pb-2">
          <div className="min-w-0">
            {title != null ? (
              <h3 className="font-display text-h2 font-semibold text-ink">{title}</h3>
            ) : null}
            {meta != null ? (
              <p className="mt-0.5 text-xs text-ink-3">{meta}</p>
            ) : null}
          </div>
          {actions != null ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </header>
      ) : null}
      <div className={flush ? "" : "px-4 pb-4 pt-2 first:pt-4"}>{children}</div>
    </section>
  );
}

export default Card;
