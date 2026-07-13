// Region 5 — AttemptsCard. The payment attempts of the currently-selected contribution,
// read from the SOURCE `scheduledContributions/{id}/attempts` subcollection (specs/04 §4.8)
// — the authoritative record of each two-phase payment attempt, never a projection. Money
// is integer cents formatted at the render boundary.

import Card from "@/components/Card";
import StatusPill from "@/components/Pill";
import { Table, type Column } from "@/components/Table";
import { formatCents } from "@/lib/format";
import type {
  CommandContribution,
  CommandPaymentAttempt,
} from "@/lib/commandTypes";
import { formatDateTime } from "@/components/loans/detail/time";

export interface AttemptsCardProps {
  contribution: CommandContribution | null;
  attempts: CommandPaymentAttempt[];
  loading: boolean;
}

function pad(n: number): string {
  return n.toString().padStart(3, "0");
}

const COLUMNS: Column<CommandPaymentAttempt>[] = [
  {
    key: "attempt",
    header: "Attempt",
    numeric: true,
    render: (a) => `att_${pad(a.attemptNumber)}`,
  },
  {
    key: "amount",
    header: "Amount",
    align: "right",
    numeric: true,
    render: (a) => formatCents(a.requestedAmountCents),
  },
  {
    key: "status",
    header: "Status",
    render: (a) => <StatusPill status={a.status} />,
  },
  {
    key: "ref",
    header: "Processor ref",
    numeric: true,
    render: (a) =>
      a.processorReference ?? <span className="text-ink-3">—</span>,
  },
  {
    key: "failure",
    header: "Failure",
    render: (a) =>
      a.failureCode ? (
        <span className="font-mono text-xs text-critical">{a.failureCode}</span>
      ) : (
        <span className="text-ink-3">—</span>
      ),
  },
  {
    key: "when",
    header: "When",
    numeric: true,
    render: (a) => formatDateTime(a.completedAt ?? a.startedAt),
  },
];

export function AttemptsCard({ contribution, attempts, loading }: AttemptsCardProps) {
  if (!contribution) {
    return (
      <Card title="Payment attempts">
        <p className="py-6 text-center text-sm text-ink-3">
          Select a contribution above to see its payment attempts.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Payment attempts"
      meta={`installment ${pad(contribution.installmentNumber)}`}
      flush
    >
      <Table<CommandPaymentAttempt>
        caption={`Payment attempts for installment ${pad(contribution.installmentNumber)}`}
        columns={COLUMNS}
        rows={attempts}
        rowKey={(a) => a.id}
        loading={loading}
        skeletonRows={2}
        emptyMessage="No attempts yet — none has been submitted for this installment."
      />
    </Card>
  );
}

export default AttemptsCard;
