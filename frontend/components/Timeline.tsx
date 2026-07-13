// Timeline — the servicing activity feed (specs/04 §4.9). A vertical connector with
// a colored rail dot per item, keyed by eventType (via the shared statusMeta) or by
// a numeric severityRank, plus actor + timestamp. Color never stands alone: each item
// shows a title and (for events) the mapped label. Timestamps are passed
// pre-formatted by the caller.

import type { ReactNode } from "react";
import { eventTypeMeta, severityMeta, solidBg } from "@/components/statusMeta";

export interface TimelineItem {
  id: string;
  /** Servicing event type — selects the rail color + a default label. */
  eventType?: string;
  /** Alternative color source (exceptions) — takes precedence when set. */
  severityRank?: number;
  /** Primary line. Defaults to the mapped event label when omitted. */
  title?: ReactNode;
  /** Optional secondary detail line. */
  detail?: ReactNode;
  /** Who performed it (user display name or "System"). */
  actor?: ReactNode;
  /** Pre-formatted timestamp. */
  timestamp?: ReactNode;
}

export interface TimelineProps {
  items: TimelineItem[];
  emptyMessage?: ReactNode;
  className?: string;
}

export function Timeline({
  items,
  emptyMessage = "No activity yet.",
  className,
}: TimelineProps) {
  if (items.length === 0) {
    return <p className="py-6 text-center text-sm text-ink-3">{emptyMessage}</p>;
  }
  return (
    <ol
      className={["relative space-y-0", className ?? ""].filter(Boolean).join(" ")}
    >
      {items.map((item, i) => {
        const meta =
          typeof item.severityRank === "number"
            ? severityMeta(item.severityRank)
            : eventTypeMeta(item.eventType ?? "");
        const isLast = i === items.length - 1;
        return (
          <li key={item.id} className="relative flex gap-3 pb-4">
            {/* rail: dot + connector */}
            <div className="flex flex-col items-center">
              <span
                aria-hidden="true"
                className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-surface ${solidBg(
                  meta.token,
                )}`}
              />
              {isLast ? null : (
                <span
                  aria-hidden="true"
                  className="mt-1 w-px flex-1 bg-border"
                />
              )}
            </div>
            <div className="min-w-0 flex-1 pb-1">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium text-ink">
                  {item.title ?? meta.label}
                </span>
                {item.timestamp != null ? (
                  <span className="shrink-0 font-mono text-xs tabular-nums text-ink-3">
                    {item.timestamp}
                  </span>
                ) : null}
              </div>
              {item.detail != null ? (
                <div className="mt-0.5 text-sm text-ink-2">{item.detail}</div>
              ) : null}
              {item.actor != null ? (
                <div className="mt-0.5 text-xs text-ink-3">{item.actor}</div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default Timeline;
