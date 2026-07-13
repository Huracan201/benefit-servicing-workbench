// Meter — a single horizontal utilization bar (e.g. employer commitment: posted ÷
// committed). Zero-dependency; the fill uses the accent token by default (chrome, not
// a "good" signal). Exposes role="meter" with aria-valuenow/min/max; the numeric
// value label is rendered so the reading is never color/length alone. Pass integer
// percentages or raw value+max (money as integer cents).

import type { ReactNode } from "react";
import { solidBg, type ColorToken } from "@/components/statusMeta";

export interface MeterProps {
  label: ReactNode;
  value: number;
  /** Denominator (default 100 — i.e. `value` is already a percent). */
  max?: number;
  token?: ColorToken;
  /** Pre-formatted value label (e.g. "74%"); defaults to a rounded percent. */
  valueLabel?: ReactNode;
  className?: string;
}

export function Meter({
  label,
  value,
  max = 100,
  token = "accent",
  valueLabel,
  className,
}: MeterProps) {
  // max <= 0 is an UNKNOWN ratio (e.g. zero-commitment employer), not a full bar:
  // render 0% so it agrees with the "—" value label callers show in that case.
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const shownLabel = valueLabel ?? `${Math.round(pct)}%`;
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-center justify-between text-sm">
        <span className="text-ink-2">{label}</span>
        <span className="font-mono tabular-nums text-ink">{shownLabel}</span>
      </div>
      <div
        role="meter"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={typeof label === "string" ? label : undefined}
        className="h-2.5 overflow-hidden rounded-pill border border-border bg-surface-2"
      >
        <div
          className={`h-full rounded-pill ${solidBg(token)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default Meter;
