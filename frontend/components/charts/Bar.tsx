// Bar (paired) — a zero-dependency inline-SVG grouped bar chart comparing two series
// per category (e.g. scheduled vs. posted per month, from integer cents). Each series
// carries a reserved token via `currentColor`; a built-in Legend keeps the mapping
// explicit (never color alone). A single baseline anchors the bars. Values are raw
// magnitudes (cents/counts); the chart shows relative height only.
//
// The plot SVG uses preserveAspectRatio="none" so its viewBox stretches to the full
// card width — grouped bars and the category labels beneath share the same 1/N cells
// and stay aligned. An OPTIONAL quantitative value axis (`valueTicks` + `formatValue`)
// renders horizontal gridlines in the SVG plus crisp HTML tick labels in a left gutter
// (kept out of the distorted SVG so the text is never stretched).

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
  /** Draw N value gridlines (0 baseline → max) with tick labels. 0 disables the axis. */
  valueTicks?: number;
  /** Format a tick value (integer cents) for its axis label. Required with valueTicks. */
  formatValue?: (value: number) => string;
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
  valueTicks = 0,
  formatValue,
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

  // Value axis (opt-in): fractions 0..1 of maxVal, each mapped to its plot-area y.
  // Values stay integer cents until `fmt` at the render boundary. Capturing formatValue
  // in a const keeps its narrowing inside the Array.from callback under strict TS.
  const fmt = formatValue;
  const ticks =
    valueTicks > 0 && fmt
      ? Array.from({ length: valueTicks + 1 }, (_, i) => {
          const fraction = i / valueTicks;
          return {
            i,
            y: axisY - fraction * usableH,
            label: fmt(Math.round(maxVal * fraction)),
          };
        })
      : [];

  return (
    <div className={className}>
      <div className="flex items-start gap-2">
        {ticks.length ? (
          <div
            aria-hidden="true"
            className="relative shrink-0 w-[4.5rem]"
            style={{ height }}
          >
            {ticks.map((t) => (
              <span
                key={t.i}
                className="absolute right-0 -translate-y-1/2 whitespace-nowrap pr-1 text-[10px] leading-none tabular-nums text-ink-3"
                style={{ top: t.y }}
              >
                {t.label}
              </span>
            ))}
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <svg
            width="100%"
            height={height}
            viewBox={`0 0 ${totalW} ${height}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`${aLabel} vs. ${bLabel} by category`}
          >
            {/* Interior + top value gridlines (dashed); the baseline below is 0. */}
            {ticks
              .filter((t) => t.i > 0)
              .map((t) => (
                <line
                  key={`grid-${t.i}`}
                  x1={0}
                  y1={t.y}
                  x2={totalW}
                  y2={t.y}
                  stroke="currentColor"
                  strokeWidth="1"
                  strokeDasharray="2 3"
                  className="text-border"
                />
              ))}
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
        </div>
      </div>
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
