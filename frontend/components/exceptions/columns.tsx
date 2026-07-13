// Column definitions for the exception-workbench DenseTable (specs/15 §15.1, §15.3).
// The row reads: Entity (the borrower, a real link to the account) · Type (the machine
// category) · Exception (a severity rail + the human summary, via SeverityCell keyed on
// the NUMERIC severityRank — specs/04 §4.10) · Status (a labeled pill) · Assignee
// (frontend-only identity) · Age · Actions (the command cluster).
//
// a11y: the Entity cell holds the row's REAL <a> (next/link) to the account, so keyboard
// and assistive-tech users reach it without any whole-row click affordance (the row is
// action-dense, so it is deliberately NOT wholesale-clickable — that would collide with
// the in-row command buttons/dialogs). Every status/severity is carried by a text label,
// never color alone (specs/15 §15.1).

import type { ReactNode } from "react";
import Link from "next/link";
import type { Column } from "@/components/Table";
import { SeverityCell } from "@/components/SeverityCell";
import { StatusPill } from "@/components/Pill";
import { humanize } from "@/components/statusMeta";
import { AssigneeCell } from "@/components/exceptions/AssigneeCell";
import { ExceptionRowActions } from "@/components/exceptions/ExceptionRowActions";
import {
  formatRelativeAge,
  formatTimestamp,
  type ExceptionRow,
} from "@/lib/readExceptions";
import type { Role } from "@/lib/types";

export interface ExceptionColumnsContext {
  /** Viewer role (affordance only). */
  role: Role | null;
  /** Viewer uid (for the frontend-only assignee rule). */
  currentUid: string | null;
}

function EntityCell({ row }: { row: ExceptionRow }): ReactNode {
  const primary = row.borrowerName?.trim() || row.entityId;
  const secondary = row.employerName?.trim();
  return (
    <div className="min-w-0 max-w-[15rem]">
      {row.loanId ? (
        <Link
          href={`/loans/${row.loanId}`}
          className="block truncate font-medium text-ink underline-offset-2 hover:text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        >
          {primary}
        </Link>
      ) : (
        <span className="block truncate font-medium text-ink">{primary}</span>
      )}
      {secondary ? (
        <span className="block truncate text-xs text-ink-3">{secondary}</span>
      ) : null}
    </div>
  );
}

function ExceptionSubtitle({ row }: { row: ExceptionRow }): ReactNode {
  return (
    <>
      <span className="font-mono">{row.entityId}</span>
      {row.occurrenceCount > 1 ? (
        <span>{` · seen ${row.occurrenceCount}×`}</span>
      ) : null}
    </>
  );
}

/** Build the workbench columns bound to the current viewer (role + uid). */
export function exceptionColumns({
  role,
  currentUid,
}: ExceptionColumnsContext): Column<ExceptionRow>[] {
  return [
    {
      key: "entity",
      header: "Entity",
      render: (r) => <EntityCell row={r} />,
    },
    {
      key: "type",
      header: "Type",
      render: (r) => <span className="text-ink-2">{humanize(r.exceptionType)}</span>,
    },
    {
      key: "exception",
      header: "Exception",
      render: (r) => (
        <SeverityCell
          severityRank={r.severityRank}
          title={r.summary}
          subtitle={<ExceptionSubtitle row={r} />}
        />
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => <StatusPill status={r.status} />,
    },
    {
      key: "assignee",
      header: "Assignee",
      render: (r) => <AssigneeCell assignedTo={r.assignedTo} currentUid={currentUid} />,
    },
    {
      key: "age",
      header: "Age",
      align: "right",
      numeric: true,
      width: "5rem",
      render: (r) => (
        <span className="text-ink-2" title={formatTimestamp(r.createdAt)}>
          {formatRelativeAge(r.createdAt)}
        </span>
      ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      render: (r) => (
        <ExceptionRowActions exception={r} role={role} currentUid={currentUid} />
      ),
    },
  ];
}
