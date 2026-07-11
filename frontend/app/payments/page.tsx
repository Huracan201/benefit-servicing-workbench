import StatusBadge from "@/components/StatusBadge";
import Table, { type Column } from "@/components/Table";
import { CONTRIBUTION_STATUSES, type ScheduledContribution } from "@/lib/types";

// Payment operations queue stub (specs/15 §15.3). In Phase 4 each tab is a
// paginated subscription on `status (+ employerId) + scheduledDate`. Tabs mirror
// the contribution status enum.

type Row = Pick<
  ScheduledContribution,
  "installmentNumber" | "borrowerName" | "employerName" | "status"
> & { id: string };

const COLUMNS: Column<Row>[] = [
  { key: "borrower", header: "Borrower", render: (r) => r.borrowerName },
  { key: "employer", header: "Employer", render: (r) => r.employerName },
  { key: "scheduledDate", header: "Scheduled date", render: () => "—" },
  { key: "amount", header: "Amount", align: "right", numeric: true, render: () => "—" },
  { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
  { key: "attempts", header: "Attempts", align: "right", numeric: true, render: () => "—" },
  { key: "failure", header: "Failure reason", render: () => "—" },
  { key: "updated", header: "Last updated", render: () => "—" },
];

export default function PaymentsPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Payment operations</h1>
        <p className="text-sm text-content-muted">
          Each status tab is a paginated, index-backed subscription (specs/15 §15.3).
        </p>
      </header>

      <div
        role="tablist"
        aria-label="Contribution status"
        className="flex flex-wrap gap-2 border-b border-border pb-2"
      >
        {CONTRIBUTION_STATUSES.map((status, i) => (
          <button
            key={status}
            type="button"
            role="tab"
            aria-selected={i === 0}
            className={[
              "rounded-md px-3 py-1.5 text-sm",
              i === 0
                ? "bg-accent/10 text-accent ring-1 ring-inset ring-accent/30"
                : "text-content-muted hover:bg-surface-muted",
            ].join(" ")}
          >
            <StatusBadge status={status} />
          </button>
        ))}
      </div>

      <Table<Row>
        caption="Payment queue"
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        emptyMessage="Contribution rows load per-tab from scheduledContributions in Phase 4."
      />
    </div>
  );
}
