"use client";

// ExceptionRowActions — the per-row operator command cluster for the exception
// workbench (specs/06 §6.4, specs/12 §12.2). Every action runs through `useCommand`
// (the single write-path engine: idempotency-key discipline, async-poll handling, toast
// feedback) dispatched into the typed command client — never a raw fetch, never a direct
// document write (specs/02 P1). Affordance is UX only: all four exception commands
// require OPERATIONS_USER, so a locked button is a hint, not the boundary — the server
// authorizes and a real 403 still surfaces as a typed toast (specs/12 §12.5).
//
// State-machine gating (specs/06 §6.4): OPEN/IN_REVIEW are actionable; RESOLVED and
// DISMISSED are terminal (no actions). `mark-in-review` is offered only from OPEN.
// Assignment is status-neutral, so "Assign to me" shows on any actionable row that isn't
// already mine. Because each row renders its own component instance, its four useCommand
// hooks are a fixed, unconditional set (Rules of Hooks safe).
//
// A row is one component instance, so its confirm/form dialogs live INSIDE the row. They
// render as fixed-overlay modals (position:fixed escapes the table's overflow container),
// and close themselves once the command settles — at which point the live source
// subscription reflects the landed status/assignee (specs/05 §5.7), never a projection.

import { ConfirmAction } from "@/components/ConfirmAction";
import { CommandButton } from "@/components/CommandButton";
import { CommandFormDialog } from "@/components/CommandFormDialog";
import { useCommand } from "@/hooks/useCommand";
import type { ExceptionRow } from "@/lib/readExceptions";
import type { Role } from "@/lib/types";

export interface ExceptionRowActionsProps {
  exception: ExceptionRow;
  /** Viewer role from the Firebase custom claim (affordance only). */
  role: Role | null;
  /** Viewer uid — hides "Assign to me" when the row is already assigned to the viewer. */
  currentUid: string | null;
}

export function ExceptionRowActions({
  exception,
  role,
  currentUid,
}: ExceptionRowActionsProps) {
  const id = exception.id;
  const status = exception.status;

  // A fixed set of four handles, one per exception command (Rules of Hooks: never
  // conditional). onSettled is intentionally omitted — the live source subscription
  // reflects the mutation; there is nothing to refetch (specs/05 §5.7).
  const assign = useCommand({ action: "assignException", id, role });
  const review = useCommand({ action: "markExceptionInReview", id, role });
  const resolve = useCommand({ action: "resolveException", id, role });
  const dismiss = useCommand({ action: "dismissException", id, role });

  const terminal = status === "RESOLVED" || status === "DISMISSED";
  if (terminal) {
    return <span className="text-ink-3">—</span>;
  }

  const alreadyMine = currentUid != null && exception.assignedTo === currentUid;
  const canReview = status === "OPEN";

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {!alreadyMine ? (
        <CommandButton handle={assign} size="sm">
          Assign to me
        </CommandButton>
      ) : null}
      {canReview ? (
        <CommandButton handle={review} size="sm">
          Review
        </CommandButton>
      ) : null}
      <CommandButton handle={resolve} size="sm">
        Resolve
      </CommandButton>
      <CommandButton handle={dismiss} size="sm">
        Dismiss
      </CommandButton>

      {/* Assign to me — no typed body (omitting assignToUid = assign to the caller,
          openapi AssignExceptionRequest). Status-neutral (specs/06 §6.4). */}
      <ConfirmAction
        handle={assign}
        title="Assign to me"
        body="You'll be recorded as the assignee. This doesn't change the exception's status."
        confirmLabel="Assign to me"
      />
      {/* Mark in review — OPEN → IN_REVIEW, no body. */}
      <ConfirmAction
        handle={review}
        title="Move to in review"
        body="Mark this exception as under investigation."
        confirmLabel="Mark in review"
      />
      {/* Resolve — requires a non-empty note (openapi ResolveExceptionRequest.note). */}
      <CommandFormDialog
        handle={resolve}
        title="Resolve exception"
        submitLabel="Resolve"
        fields={[
          {
            name: "note",
            label: "Resolution note",
            type: "textarea",
            required: true,
            placeholder: "Describe how this exception was resolved…",
          },
        ]}
      />
      {/* Dismiss (danger; terminal) — requires a non-empty reason
          (openapi DismissExceptionRequest.reason). */}
      <CommandFormDialog
        handle={dismiss}
        title="Dismiss exception"
        submitLabel="Dismiss"
        fields={[
          {
            name: "reason",
            label: "Dismissal reason",
            type: "textarea",
            required: true,
            placeholder: "Explain why this exception is being dismissed…",
          },
        ]}
      />
    </div>
  );
}

export default ExceptionRowActions;
