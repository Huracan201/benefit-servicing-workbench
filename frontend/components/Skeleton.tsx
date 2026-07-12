// Skeleton — loading placeholders. Uses a subtle pulse that is disabled under
// prefers-reduced-motion (motion-reduce:animate-none).

export interface SkeletonProps {
  className?: string;
  /** Fully round (avatars / dots). */
  circle?: boolean;
}

export function Skeleton({ className, circle }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={[
        "block animate-pulse bg-ink/[0.08] motion-reduce:animate-none",
        circle ? "rounded-full" : "rounded-sm",
        className ?? "h-4 w-full",
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}

export interface SkeletonTextProps {
  /** Number of lines to render. */
  lines?: number;
  className?: string;
}

/** A short stack of skeleton lines, the last one shortened. */
export function SkeletonText({ lines = 3, className }: SkeletonTextProps) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={["block space-y-2", className ?? ""].filter(Boolean).join(" ")}
    >
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={i === lines - 1 ? "h-3.5 w-2/3" : "h-3.5 w-full"} />
      ))}
    </span>
  );
}

export default Skeleton;
