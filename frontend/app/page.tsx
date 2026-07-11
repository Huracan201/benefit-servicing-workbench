import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";

// Dashboard stub (specs/15 §15.3). In Phase 4 this subscribes to
// `portfolioSummaries/current` + the current-period doc (2 docs) and renders the
// portfolio-health tiles and charts. For the foundation scaffold it renders the
// shell, tile layout, and status legend so the design direction is visible.

const TILES: { label: string; hint: string }[] = [
  { label: "Active loans", hint: "portfolioSummaries/current" },
  { label: "Active benefit agreements", hint: "portfolioSummaries/current" },
  { label: "Scheduled this month", hint: "portfolioSummaries/{period}" },
  { label: "Posted this month", hint: "portfolioSummaries/{period}" },
  { label: "Failed contributions", hint: "portfolioSummaries/current" },
  { label: "Open exceptions", hint: "portfolioSummaries/current" },
];

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-content-muted">
          Portfolio health. Aggregates are eventually consistent and may lag a few
          seconds behind a just-completed action (specs/05 §5.7).
        </p>
      </header>

      <section
        aria-label="Portfolio tiles"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        {TILES.map((tile) => (
          <div
            key={tile.label}
            className="rounded-lg border border-border bg-surface-raised p-4"
          >
            <div className="text-sm text-content-muted">{tile.label}</div>
            <div className="mt-2 text-2xl font-semibold tabular-nums">—</div>
            <div className="mt-1 text-xs text-content-muted">{tile.hint}</div>
          </div>
        ))}
      </section>

      <section aria-label="Status legend" className="space-y-2">
        <h2 className="text-sm font-medium text-content-muted">
          Contribution status legend
        </h2>
        <div className="flex flex-wrap gap-2">
          {["SCHEDULED", "PROCESSING", "POSTED", "FAILED", "RETRY_PENDING", "CANCELED"].map(
            (s) => (
              <StatusBadge key={s} status={s} />
            ),
          )}
        </div>
      </section>

      <nav aria-label="Quick links" className="flex flex-wrap gap-3 text-sm">
        <Link className="text-accent hover:underline" href="/loans">
          Open loan portfolio →
        </Link>
        <Link className="text-accent hover:underline" href="/payments">
          Open payment queue →
        </Link>
        <Link className="text-accent hover:underline" href="/exceptions">
          Open exception workbench →
        </Link>
      </nav>
    </div>
  );
}
