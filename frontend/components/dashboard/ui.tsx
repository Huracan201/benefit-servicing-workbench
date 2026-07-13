// Shared presentational bits for the dashboard: the error surface (specs/15 §15.2 —
// every subscription must be able to surface its error) and the loading skeletons
// (skeleton placeholders, never spinners). All motion respects prefers-reduced-motion
// via the Skeleton primitive's motion-reduce guard.

import Skeleton from "@/components/Skeleton";

/** A surfaced read error for a dashboard section. Renders nothing when `error` is null. */
export function SectionError({
  error,
  context,
}: {
  error: Error | null;
  context: string;
}) {
  if (!error) return null;
  return (
    <div
      role="alert"
      className="rounded border border-critical/[0.4] bg-critical/[0.1] px-3.5 py-3 text-sm text-critical"
    >
      <span className="font-semibold">Couldn’t load {context}.</span>{" "}
      <span className="text-ink-2">{error.message}</span>
    </div>
  );
}

/** One KPI-tile skeleton (label + hero placeholder). */
export function TileSkeleton() {
  return (
    <div className="rounded border border-border bg-surface p-3.5 shadow-elevation">
      <Skeleton className="h-2.5 w-24" />
      <Skeleton className="mt-3 h-6 w-20" />
      <Skeleton className="mt-3 h-3 w-28" />
    </div>
  );
}

/** A full row of tile skeletons matching the live KPI grid. */
export function KpiSkeletonGrid({ count = 8 }: { count?: number }) {
  return (
    <div
      role="status"
      aria-label="Loading portfolio metrics"
      className="grid grid-cols-2 gap-3 min-[1101px]:grid-cols-4"
    >
      {Array.from({ length: count }).map((_, i) => (
        <TileSkeleton key={i} />
      ))}
    </div>
  );
}

/** A block skeleton sized for a chart body. */
export function ChartSkeleton({ height = 160 }: { height?: number }) {
  return (
    <div role="status" aria-label="Loading chart" className="space-y-2">
      <div style={{ height }}>
        <Skeleton className="h-full w-full" />
      </div>
      <Skeleton className="h-3 w-1/3" />
    </div>
  );
}

/** A stack of line skeletons for the meter / timeline lists. */
export function RowsSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-3.5">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="space-y-1.5">
          <div className="flex justify-between">
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-3 w-24" />
          </div>
          <Skeleton className="h-2.5 w-full rounded-pill" />
        </div>
      ))}
    </div>
  );
}
