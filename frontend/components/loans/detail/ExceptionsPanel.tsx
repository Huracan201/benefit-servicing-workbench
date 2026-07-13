"use client";

// Region 6 — ExceptionsPanel. This loan's operational exceptions (specs/06 §6.4), read
// from the SOURCE `operationalExceptions` docs (severityRank DESC via the read hook) — a
// SeverityCell keeps severity legible without relying on color. Per-exception commands
// (Assign to me / Mark in review / Resolve / Dismiss) each run through a `useCommand`
// handle → the typed command client (CQRS, specs/02 P1); the two form actions collect a
// required note/reason. Role-gated for UX only; the server still authorizes each write.

import Card from "@/components/Card";
import Skeleton from "@/components/Skeleton";
import CommandButton from "@/components/CommandButton";
import CommandFormDialog, {
  type FieldSpec,
} from "@/components/CommandFormDialog";
import ConfirmAction from "@/components/ConfirmAction";
import StatusPill from "@/components/Pill";
import SeverityCell from "@/components/SeverityCell";
import { humanize } from "@/components/statusMeta";
import { useCommand } from "@/hooks/useCommand";
import type { CommandOperationalException } from "@/lib/commandTypes";
import type { Role } from "@/lib/types";
import { formatDateTime } from "@/components/loans/detail/time";

export interface ExceptionsPanelProps {
  exceptions: CommandOperationalException[];
  loading: boolean;
  role: Role | null;
}

const TERMINAL = new Set(["RESOLVED", "DISMISSED"]);

const RESOLVE_FIELDS: FieldSpec[] = [
  {
    name: "note",
    label: "Resolution note",
    type: "textarea",
    required: true,
    placeholder: "How was this resolved?",
  },
];

const DISMISS_FIELDS: FieldSpec[] = [
  {
    name: "reason",
    label: "Dismissal reason",
    type: "textarea",
    required: true,
    placeholder: "Why is this being dismissed?",
  },
  {
    name: "note",
    label: "Note (optional)",
    type: "textarea",
    placeholder: "Additional context",
  },
];

/** The command cluster for one exception. Owns its four `useCommand` handles at a stable
 *  position (one component instance per exception → hook order never shifts). */
function ExceptionActions({
  exception,
  role,
}: {
  exception: CommandOperationalException;
  role: Role | null;
}) {
  const id = exception.id;
  const assign = useCommand({ action: "assignException", id, role });
  const markInReview = useCommand({ action: "markExceptionInReview", id, role });
  const resolve = useCommand({ action: "resolveException", id, role });
  const dismiss = useCommand({ action: "dismissException", id, role });

  if (TERMINAL.has(exception.status)) {
    return null;
  }

  return (
    <div className="mt-2 flex flex-wrap justify-end gap-2">
      <CommandButton handle={assign} size="sm">
        Assign to me
      </CommandButton>
      <ConfirmAction
        handle={assign}
        title="Assign to me"
        body="Assign this exception to yourself?"
      />

      {exception.status === "OPEN" ? (
        <>
          <CommandButton handle={markInReview} size="sm" />
          <ConfirmAction
            handle={markInReview}
            body="Move this exception into review?"
          />
        </>
      ) : null}

      <CommandButton handle={resolve} size="sm" />
      <CommandFormDialog
        handle={resolve}
        fields={RESOLVE_FIELDS}
        title="Resolve exception"
        submitLabel="Resolve"
      />

      <CommandButton handle={dismiss} size="sm" />
      <CommandFormDialog
        handle={dismiss}
        fields={DISMISS_FIELDS}
        title="Dismiss exception"
        submitLabel="Dismiss"
      />
    </div>
  );
}

function ExceptionRow({
  exception,
  role,
}: {
  exception: CommandOperationalException;
  role: Role | null;
}) {
  return (
    <li className="border-b border-border px-4 py-3 last:border-0">
      <div className="flex items-start justify-between gap-3">
        <SeverityCell
          className="min-w-0 flex-1"
          severityRank={exception.severityRank}
          title={exception.summary}
          subtitle={`${humanize(exception.exceptionType)} · ${exception.entityId}`}
        />
        <div className="flex shrink-0 flex-col items-end gap-1">
          <StatusPill status={exception.status} />
          <span className="font-mono text-xs tabular-nums text-ink-3">
            {formatDateTime(exception.lastSeenAt)}
          </span>
        </div>
      </div>
      <ExceptionActions exception={exception} role={role} />
    </li>
  );
}

export function ExceptionsPanel({ exceptions, loading, role }: ExceptionsPanelProps) {
  const open = exceptions.filter((e) => !TERMINAL.has(e.status)).length;

  return (
    <Card
      title="Operational exceptions"
      actions={
        open > 0 ? <StatusPill status="OPEN" label={`${open} open`} /> : undefined
      }
      flush
    >
      {loading ? (
        <div>
          <span role="status" className="sr-only">
            Loading exceptions
          </span>
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              aria-hidden="true"
              className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 last:border-0"
            >
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <Skeleton className="h-5 w-[3px] shrink-0" />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
              <Skeleton className="h-5 w-20 shrink-0" />
            </div>
          ))}
        </div>
      ) : exceptions.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-3">
          No operational exceptions on this loan.
        </p>
      ) : (
        <ul>
          {exceptions.map((e) => (
            <ExceptionRow key={e.id} exception={e} role={role} />
          ))}
        </ul>
      )}
    </Card>
  );
}

export default ExceptionsPanel;
