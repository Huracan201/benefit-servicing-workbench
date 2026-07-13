"use client";

// Region 8 — NotesPanel. The loan's internal notes (append-only) read from the SOURCE
// `loans/{loanId}/notes` subcollection (newest first). "Add note" runs through a
// `useCommand` handle → addLoanNote (CQRS, specs/02 P1). Add-note is a no-confirm action
// that still needs a typed body, so its trigger only ARMS the intent (mint + freeze the
// Idempotency-Key, specs/08) and the CommandFormDialog collects the required text before
// submitting — a plain CommandButton would auto-submit an empty note. Role-gated for UX
// only; the server authorizes the write.

import Button from "@/components/Button";
import Card from "@/components/Card";
import Skeleton from "@/components/Skeleton";
import CommandFormDialog, {
  type FieldSpec,
} from "@/components/CommandFormDialog";
import { useCommand } from "@/hooks/useCommand";
import type { CommandNote } from "@/lib/commandTypes";
import type { Role } from "@/lib/types";
import { formatDateTime } from "@/components/loans/detail/time";

export interface NotesPanelProps {
  notes: CommandNote[];
  loading: boolean;
  loanId: string;
  role: Role | null;
}

const NOTE_FIELDS: FieldSpec[] = [
  {
    name: "text",
    label: "Note",
    type: "textarea",
    required: true,
    placeholder: "Add an internal note for the audit trail",
  },
];

export function NotesPanel({ notes, loading, loanId, role }: NotesPanelProps) {
  const add = useCommand({ action: "addLoanNote", id: loanId, role });

  // Arm-only trigger: opens the form dialog (which submits the typed body). A locked,
  // focusable button carries the permission reason when the role is insufficient.
  const trigger = add.permitted ? (
    <Button variant="primary" onClick={() => add.arm()} className="!px-2 !py-1 !text-xs">
      Add note
    </Button>
  ) : (
    <Button
      variant="primary"
      locked
      lockedReason={`Requires ${add.meta.requires}`}
      className="!px-2 !py-1 !text-xs"
    >
      Add note
    </Button>
  );

  return (
    <Card title="Internal notes" meta="append-only" actions={trigger} flush>
      <CommandFormDialog
        handle={add}
        fields={NOTE_FIELDS}
        title="Add note"
        submitLabel="Add note"
      />
      {loading ? (
        <div>
          <span role="status" className="sr-only">
            Loading notes
          </span>
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              aria-hidden="true"
              className="space-y-2 border-b border-border px-4 py-3 last:border-0"
            >
              <div className="flex items-baseline justify-between gap-3">
                <Skeleton className="h-3.5 w-24" />
                <Skeleton className="h-3.5 w-16" />
              </div>
              <Skeleton className="h-3.5 w-full" />
              <Skeleton className="h-3.5 w-2/3" />
            </div>
          ))}
        </div>
      ) : notes.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-3">No notes yet.</p>
      ) : (
        <ul>
          {notes.map((note) => (
            <li key={note.id} className="border-b border-border px-4 py-3 last:border-0">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-xs font-semibold text-ink-2">
                  {note.authorName?.trim() || note.authorId}
                </span>
                <span className="font-mono text-xs tabular-nums text-ink-3">
                  {formatDateTime(note.createdAt)}
                </span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-ink">{note.text}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default NotesPanel;
