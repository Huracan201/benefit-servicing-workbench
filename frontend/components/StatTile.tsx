// StatTile — a KPI tile (U1 design): an uppercase micro label, a mono hero number,
// an optional sub-line, and an optional inline chart slot (e.g. a Sparkline). The
// hero number is passed already-formatted (money is formatted from integer cents at
// the render boundary — never store floats). `tone` optionally colors the number for
// attention (e.g. `critical` for a failure count) — the sub-line still carries meaning.

import type { ReactNode } from "react";
import { inkColor, type ColorToken } from "@/components/statusMeta";

export interface StatTileProps {
  /** Uppercase micro label. */
  label: ReactNode;
  /** Pre-formatted hero value (mono). */
  value: ReactNode;
  /** Optional sub-line under the value. */
  sub?: ReactNode;
  /** Optional inline chart slot (rendered under the value, e.g. a Sparkline). */
  chart?: ReactNode;
  /** Optional top-right slot (e.g. a StatusPill or a delta). */
  aside?: ReactNode;
  /** Color the hero number for emphasis (default: ink). */
  tone?: ColorToken;
  className?: string;
}

export function StatTile({
  label,
  value,
  sub,
  chart,
  aside,
  tone,
  className,
}: StatTileProps) {
  return (
    <div
      className={[
        "rounded border border-border bg-surface p-3.5 shadow-elevation",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-micro font-semibold uppercase tracking-wide text-ink-3">
          {label}
        </span>
        {aside != null ? <span className="shrink-0">{aside}</span> : null}
      </div>
      <div
        className={[
          "mt-2 font-mono text-hero font-semibold tabular-nums",
          tone ? inkColor(tone) : "text-ink",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {value}
      </div>
      {chart != null ? <div className="mt-1.5">{chart}</div> : null}
      {sub != null ? <div className="mt-1 text-xs text-ink-2">{sub}</div> : null}
    </div>
  );
}

export default StatTile;
