// Sparkline — a zero-dependency inline-SVG trend line derived from a numeric series
// (e.g. 6 months of scheduled cents). Color comes from a token via `currentColor`
// (default: the Verdigris accent, which is chrome here — a trend, not a "good"
// signal). Decorative by default; pass `ariaLabel` to expose a summary. Money series
// are passed as integer cents; the sparkline only shows shape, never a value.

import { inkColor, type ColorToken } from "@/components/statusMeta";

export interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  token?: ColorToken;
  /** Accessible summary; when omitted the chart is aria-hidden (decorative). */
  ariaLabel?: string;
  className?: string;
}

export function Sparkline({
  values,
  width = 150,
  height = 30,
  token = "accent",
  ariaLabel,
  className,
}: SparklineProps) {
  const pad = 3;
  const w = width;
  const h = height;
  const n = values.length;

  let path = "";
  let lastX = w - pad;
  let lastY = h - pad;

  if (n >= 2) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = (w - pad * 2) / (n - 1);
    const points = values.map((v, i) => {
      const x = pad + i * stepX;
      // Invert Y (SVG origin top-left); higher value -> higher on screen.
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return [x, y] as const;
    });
    path = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const last = points[points.length - 1];
    lastX = last[0];
    lastY = last[1];
  }

  return (
    <svg
      width="100%"
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role={ariaLabel ? "img" : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      className={[inkColor(token), className ?? ""].filter(Boolean).join(" ")}
    >
      {n >= 2 ? (
        <>
          <polyline
            points={path}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
          <circle cx={lastX} cy={lastY} r="3" fill="currentColor" />
        </>
      ) : null}
    </svg>
  );
}

export default Sparkline;
