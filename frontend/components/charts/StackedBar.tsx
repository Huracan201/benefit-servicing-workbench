// StackedBar — a single proportional bar for a status mix (e.g. contribution status
// counts). Zero-dependency inline SVG with ~2px gaps between segments. The status
// palette is NOT categorical, so a direct label per segment is MANDATORY: every
// segment's label + value is rendered beneath the bar (color is never the only
// encoding). Each segment carries its own reserved token via `currentColor`.

import { inkColor, solidBg, type ColorToken } from "@/components/statusMeta";

export interface StackedSegment {
  key: string;
  /** Mandatory direct label — the mix is never conveyed by color alone. */
  label: string;
  value: number;
  token: ColorToken;
}

export interface StackedBarProps {
  segments: StackedSegment[];
  height?: number;
  /** Accessible summary for the whole bar. */
  ariaLabel?: string;
  className?: string;
}

const VB_WIDTH = 1000;
const GAP = 4; // viewBox units between segments (~2px at typical render widths)

export function StackedBar({
  segments,
  height = 26,
  ariaLabel,
  className,
}: StackedBarProps) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const positive = segments.filter((s) => s.value > 0);
  const gaps = Math.max(0, positive.length - 1) * GAP;
  const usable = VB_WIDTH - gaps;

  let x = 0;
  const rects = positive.map((seg) => {
    const w = total > 0 ? (seg.value / total) * usable : 0;
    const rect = { seg, x, w };
    x += w + GAP;
    return rect;
  });

  return (
    <div className={className}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${VB_WIDTH} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel ?? "Status mix"}
      >
        {rects.map(({ seg, x: rx, w }) => (
          <rect
            key={seg.key}
            x={rx}
            y={0}
            width={Math.max(0, w)}
            height={height}
            rx={3}
            className={inkColor(seg.token)}
            fill="currentColor"
          />
        ))}
      </svg>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-2">
        {segments.map((seg) => (
          <span key={seg.key} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`h-2 w-2 rounded-sm ${solidBg(seg.token)}`}
            />
            {seg.label}{" "}
            <b className="font-mono tabular-nums text-ink">{seg.value}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export default StackedBar;
