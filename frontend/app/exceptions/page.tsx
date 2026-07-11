import StatusBadge from "@/components/StatusBadge";
import Table, { type Column } from "@/components/Table";
import type { OperationalException } from "@/lib/types";

// Exception workbench stub (specs/15 §15.3). In Phase 4 this is a paginated
// subscription over `operationalExceptions`, default sort open · most-severe ·
// newest. Severity and status both render as labeled badges (never color alone).

type Row = Pick<
  OperationalException,
  "severity" | "exceptionType" | "borrowerName" | "employerName" | "summary" | "status"
> & { id: string };

const COLUMNS: Column<Row>[] = [
  { key: "severity", header: "Severity", render: (r) => <StatusBadge status={r.severity} /> },
  { key: "type", header: "Type", render: (r) => r.exceptionType },
  { key: "borrower", header: "Borrower", render: (r) => r.borrowerName },
  { key: "employer", header: "Employer", render: (r) => r.employerName },
  { key: "summary", header: "Summary", render: (r) => r.summary },
  { key: "assigned", header: "Assigned", render: () => "—" },
  { key: "created", header: "Created", render: () => "—" },
  { key: "status", header: "Status", render: (r) => <StatusBadge status={r.status} /> },
];

export default function ExceptionsPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Exception workbench</h1>
        <p className="text-sm text-content-muted">
          Default sort: open · most-severe · newest (specs/15 §15.3).
        </p>
      </header>
      <Table<Row>
        caption="Operational exceptions"
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.id}
        emptyMessage="Exceptions load from operationalExceptions in Phase 4."
      />
    </div>
  );
}
