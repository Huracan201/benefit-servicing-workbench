import Table, { type Column } from "@/components/Table";
import StatusBadge from "@/components/StatusBadge";
import type { LoanWorkbench } from "@/lib/types";

// Loan portfolio stub (specs/15 §15.3). In Phase 4 this subscribes to
// `loanWorkbenches` via useCollectionPage with index-backed filters (employer,
// employment status, benefit status, loan status, has-open-exception) + cursor
// pagination. The columns below match the spec's row shape.

type Row = Pick<
  LoanWorkbench,
  | "loanId"
  | "borrowerName"
  | "employerName"
  | "servicerName"
  | "benefitStatus"
  | "loanStatus"
>;

const COLUMNS: Column<Row>[] = [
  { key: "borrower", header: "Borrower", render: (r) => r.borrowerName },
  { key: "employer", header: "Employer", render: (r) => r.employerName },
  { key: "servicer", header: "Servicer", render: (r) => r.servicerName },
  {
    key: "balance",
    header: "Current balance",
    align: "right",
    numeric: true,
    render: () => "—",
  },
  {
    key: "benefitStatus",
    header: "Benefit status",
    render: (r) => <StatusBadge status={r.benefitStatus} />,
  },
  {
    key: "monthly",
    header: "Monthly contribution",
    align: "right",
    numeric: true,
    render: () => "—",
  },
  { key: "next", header: "Next contribution", render: () => "—" },
  { key: "exceptions", header: "Open exceptions", align: "right", numeric: true, render: () => "—" },
  {
    key: "loanStatus",
    header: "Loan status",
    render: (r) => <StatusBadge status={r.loanStatus} />,
  },
];

export default function LoansPage() {
  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Loan portfolio</h1>
        <p className="text-sm text-content-muted">
          Index-backed, paginated subscription over loanWorkbenches (specs/05 §5.6).
        </p>
      </header>
      <Table<Row>
        caption="Loan portfolio"
        columns={COLUMNS}
        rows={[]}
        rowKey={(r) => r.loanId}
        emptyMessage="Portfolio rows load from loanWorkbenches in Phase 4."
      />
    </div>
  );
}
