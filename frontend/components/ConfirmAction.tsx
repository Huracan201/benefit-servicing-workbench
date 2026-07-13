"use client";

// ConfirmAction — the confirm gate for a command action that needs NO typed body
// (process, retry, mark-in-review, and the lifecycle actions when the screen doesn't
// collect a reason). It renders the part-1 ConfirmDialog bound to a `useCommand` handle:
// tone from `meta`, Confirm → handle.submit(), Cancel → handle.cancel().
//
// It stays mounted across confirming → submitting → error (NOT only "confirming") so the
// dialog can show its in-flight spinner AND, crucially, so a retry after a transport
// error re-submits with the SAME frozen Idempotency-Key (specs/08) instead of re-arming a
// fresh intent (a new key could replay a mutation the server already accepted). It
// unmounts on settled/awaiting/idle, where the screen's live Firestore SOURCE
// subscription reflects the landed state — never a projection (specs/05 §5.7).

import type { ReactNode } from "react";
import ConfirmDialog from "@/components/ConfirmDialog";
import type { CommandHandle } from "@/hooks/useCommand";

export interface ConfirmActionProps {
  handle: CommandHandle;
  /** Dialog heading (default: `handle.meta.label`). */
  title?: ReactNode;
  /** Supporting description under the heading. */
  body?: ReactNode;
  /** Confirm button label (default: `handle.meta.label`). */
  confirmLabel?: string;
  /** Extra body content (e.g. a FactsGrid of what will change). */
  children?: ReactNode;
}

export function ConfirmAction({
  handle,
  title,
  body,
  confirmLabel,
  children,
}: ConfirmActionProps) {
  const { phase, meta, error } = handle;

  const open = phase === "confirming" || phase === "submitting" || phase === "error";
  if (!open || !meta.confirm) return null;

  // No role="alert": the failure is already announced assertively by the toast useCommand
  // pushes; a second live region would read the identical message twice.
  const errorNote =
    phase === "error" && error != null ? (
      <p className="text-body text-critical">{error.userMessage}</p>
    ) : null;
  // Only hand ConfirmDialog real content: it wraps non-null children in a spacing div, so
  // passing `undefined` when there's nothing to show keeps the confirm layout tight.
  const content =
    errorNote != null ? (
      <>
        {children}
        {errorNote}
      </>
    ) : (
      children
    );

  return (
    <ConfirmDialog
      open
      tone={meta.tone}
      title={title ?? meta.label}
      description={body}
      confirmLabel={confirmLabel ?? meta.label}
      loading={phase === "submitting"}
      onConfirm={() => {
        void handle.submit();
      }}
      onCancel={handle.cancel}
    >
      {content}
    </ConfirmDialog>
  );
}

export default ConfirmAction;
