"use client";

// ConfirmDialog — a focus-managed modal for confirming commands (specs/15 §15.2).
// role="dialog" aria-modal; focus moves to the primary action on open, is trapped
// while open (Tab / Shift+Tab cycle), ESC and overlay-click cancel, and focus is
// restored to the previously-focused element on close. Enter/exit motion is disabled
// under prefers-reduced-motion. `tone="danger"` styles the confirm action.

import { useCallback, useEffect, useRef, type ReactNode } from "react";
import Button from "@/components/Button";

export interface ConfirmDialogProps {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  /** Extra body content (e.g. a FactsGrid of what will change). */
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  /** Confirm is in-flight — shows a spinner and blocks re-submit. */
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const titleId = useRef(`confirm-${Math.random().toString(36).slice(2)}`).current;
  const descId = `${titleId}-desc`;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!open) return;
      if (e.key === "Escape") {
        e.preventDefault();
        if (!loading) onCancel();
        return;
      }
      if (e.key === "Tab") {
        const panel = panelRef.current;
        if (!panel) return;
        const nodes = Array.from(
          panel.querySelectorAll<HTMLElement>(FOCUSABLE),
        ).filter((el) => el.offsetParent !== null || el === document.activeElement);
        if (nodes.length === 0) return;
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    },
    [open, loading, onCancel],
  );

  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    // Focus the primary action after the panel mounts.
    const raf = requestAnimationFrame(() => confirmRef.current?.focus());
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused.current?.focus?.();
    };
  }, [open, handleKeyDown]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      // Overlay click cancels; clicks inside the panel are stopped below.
      onMouseDown={() => {
        if (!loading) onCancel();
      }}
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-ink/40"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        onMouseDown={(e) => e.stopPropagation()}
        className="relative w-full max-w-md rounded border border-border bg-surface p-5 shadow-elevation"
      >
        <h2 id={titleId} className="font-display text-h1 font-semibold text-ink">
          {title}
        </h2>
        {description != null ? (
          <p id={descId} className="mt-1.5 text-body text-ink-2">
            {description}
          </p>
        ) : null}
        {children != null ? <div className="mt-3">{children}</div> : null}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            ref={confirmRef}
            variant={tone === "danger" ? "danger" : "primary"}
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default ConfirmDialog;
