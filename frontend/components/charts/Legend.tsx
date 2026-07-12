// Legend — a small swatch + label row shared by the charts (U1 design). Because the
// status palette is NOT categorical, charts must always pair color with a direct
// label; the Legend is how paired/line charts carry that mapping.

import type { ReactNode } from "react";
import { solidBg, type ColorToken } from "@/components/statusMeta";

export interface LegendItem {
  label: ReactNode;
  token: ColorToken;
}

export interface LegendProps {
  items: LegendItem[];
  /** Optional trailing muted note (e.g. an axis span). */
  note?: ReactNode;
  className?: string;
}

export function Legend({ items, note, className }: LegendProps) {
  return (
    <div
      className={[
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-ink-2",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {items.map((item, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 rounded-sm ${solidBg(item.token)}`}
          />
          {item.label}
        </span>
      ))}
      {note != null ? <span className="text-ink-3">{note}</span> : null}
    </div>
  );
}

export default Legend;
