// Bar (paired) — a zero-dependency inline-SVG grouped bar chart comparing two series
// per category (e.g. scheduled vs. posted per month, from integer cents). Each series
// carries a reserved token via `currentColor`; a built-in Legend keeps the mapping
// explicit (never color alone). A single baseline anchors the bars. Values are raw
// magnitudes (cents/counts); the chart shows relative height only.

import { inkColor, type ColorToken } from "@/components/statusMeta";
import Legend from "@/components/charts/Legend";

export interface BarGroup {
  label: string;
  a: number;
  b: number;
}

export interface BarProps {
  data: BarGroup[];
  aLabel: string;
  bLabel: string;
  aToken?: ColorToken;
  bToken?: ColorToken;
  height?: number;
  /** Show category labels beneath the bars. */
  showLabels?: boolean;
  /** Trailing muted note on the legend (e.g. an axis span). */
  note?: string;
  className?: string;
}

const BAR_W = 14;
const INNER_GAP = 3;
const GROUP_PAD = 9; // left pad inside each group cell
const AXIS_PAD = 8; // top headroom above the tallest bar
const BASE_PAD = 14; // space under the baseline

export function Bar({
  data,
  aLabel,
  bLabel,
  aToken = "info",
  bToken = "good",
  height = 96,
  showLabels = true,
  note,
  className,
}: BarProps) {
  const groupW = GROUP_PAD * 2 + BAR_W * 2 + INNER_GAP;
  const totalW = Math.max(groupW, data.length * groupW);
  const axisY = height - BASE_PAD;
  const usableH = axisY - AXIS_PAD;
  const maxVal = Math.max(1, ...data.map((d) => Math.max(d.a, d.b)));

  function barH(v: number): number {
    return Math.max(0, (v / maxVal) * usableH);
  }

  return (
    <div className={className}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${totalW} ${height}`}
        role="img"
        aria-label={`${aLabel} vs. ${bLabel} by category`}
      >
        <line
          x1={0}
          y1={axisY}
          x2={totalW}
          y2={axisY}
          stroke="currentColor"
          strokeWidth="1"
          className="text-border"
        />
        {data.map((d, i) => {
          const gx = i * groupW + GROUP_PAD;
          const ha = barH(d.a);
          const hb = barH(d.b);
          return (
            <g key={d.label}>
              <rect
                x={gx}
                y={axisY - ha}
                width={BAR_W}
                height={ha}
                rx={3}
                className={inkColor(aToken)}
                fill="currentColor"
              />
              <rect
                x={gx + BAR_W + INNER_GAP}
                y={axisY - hb}
                width={BAR_W}
                height={hb}
                rx={3}
                className={inkColor(bToken)}
                fill="currentColor"
              />
            </g>
          );
        })}
      </svg>
      {showLabels ? (
        <div
          className="grid text-center text-xs text-ink-3"
          style={{ gridTemplateColumns: `repeat(${data.length}, minmax(0, 1fr))` }}
        >
          {data.map((d) => (
            <span key={d.label} className="truncate">
              {d.label}
            </span>
          ))}
        </div>
      ) : null}
      <Legend
        className="mt-2"
        items={[
          { label: aLabel, token: aToken },
          { label: bLabel, token: bToken },
        ]}
        note={note}
      />
    </div>
  );
}

export default Bar;
