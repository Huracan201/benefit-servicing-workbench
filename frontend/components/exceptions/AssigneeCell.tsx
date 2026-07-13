// AssigneeCell — the exception-workbench assignee display. Per the product decision,
// assignee identity is FRONTEND-ONLY: there is no backend `assignedToName` and no
// uid→name lookup (firebase/firestore.rules forbids reading other users' docs — a user
// may read only their own `users/{uid}`). So we can resolve exactly ONE identity: the
// signed-in operator. When `assignedTo` matches the viewer's uid we show "Assigned to
// me"; any other uid renders as a short-uid mono chip (a machine token, so mono +
// tabular per the U1 type system); an unassigned exception reads "Unassigned".

import { StatusPill } from "@/components/Pill";

export interface AssigneeCellProps {
  /** The exception's `assignedTo` (a firebase uid) or null when unassigned. */
  assignedTo: string | null;
  /** The signed-in operator's uid, or null when the session isn't resolved yet. */
  currentUid: string | null;
}

/** Truncate a uid to a stable short token, keeping the full value in a hover title. */
function shortUid(uid: string): string {
  return uid.length > 8 ? `${uid.slice(0, 8)}…` : uid;
}

export function AssigneeCell({ assignedTo, currentUid }: AssigneeCellProps) {
  if (!assignedTo) {
    return <span className="text-ink-3">Unassigned</span>;
  }
  if (currentUid && assignedTo === currentUid) {
    // `info` (not `accent` — accent is chrome only) tags a self-assignment.
    return <StatusPill status="ASSIGNED_TO_ME" token="info" label="Assigned to me" />;
  }
  return (
    <span
      title={assignedTo}
      className="inline-flex items-center rounded-pill border border-border bg-surface-2 px-2 py-0.5 font-mono text-xs tabular-nums text-ink-2"
    >
      {shortUid(assignedTo)}
    </span>
  );
}

export default AssigneeCell;
