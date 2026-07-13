"use client";

// ConfirmDialog — a focus-managed modal for confirming commands (specs/15 §15.2).
// role="dialog" aria-modal; focus moves to the dialog panel on open, is trapped
// while open (Tab / Shift+Tab cycle), ESC and overlay-click cancel, and focus is
// restored to the previously-focused element on close. Enter/exit motion is disabled
// under prefers-reduced-motion. `tone="danger"` styles the confirm action.

import { useEffect, useId, useRef, type ReactNode } from "react";
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
  const previouslyFocused = useRef<HTMLElement | null>(null);
  // useId() gives a stable, SSR/client-consistent id (Math.random() could mismatch on
  // hydration and break the aria-labelledby/aria-describedby linkage on first render).
  const titleId = useId();
  const descId = `${titleId}-desc`;

  // Latest values for the stable keydown handler below, so the listener never has to
  // re-bind (and never disturbs focus) when `loading` toggles mid-command.
  const loadingRef = useRef(loading);
  const onCancelRef = useRef(onCancel);
  useEffect(() => {
    loadingRef.current = loading;
    onCancelRef.current = onCancel;
  });

  // Focus capture + restore, keyed on `open` ONLY. Capturing/restoring here (not in
  // the keydown effect) means a `loading` change can never fire the cleanup that
  // restores focus to the background trigger — focus is restored solely on close.
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    // Anchor focus on the panel itself (not a button): the panel stays focusable via
    // tabIndex={-1} even while both actions are disabled during `loading`.
    const raf = requestAnimationFrame(() => panelRef.current?.focus());
    return () => {
      cancelAnimationFrame(raf);
      previouslyFocused.current?.focus?.();
    };
  }, [open]);

  // ESC-to-cancel + Tab trap: one stable handler attached once per open, reading live
  // `loading`/`onCancel` via refs. The trap treats the panel as a fallback boundary so
  // Tab keeps cycling inside the dialog even when both buttons are disabled (loading).
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (!loadingRef.current) onCancelRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      const active = document.activeElement as HTMLElement | null;
      // No focusable controls (e.g. loading — both buttons disabled): pin to the panel.
      if (focusable.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      // Entering from the panel anchor (or somehow outside): land on an edge control.
      if (active === panel || !panel.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
        return;
      }
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

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
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
        className="relative w-full max-w-md rounded border border-border bg-surface p-5 shadow-elevation outline-none"
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
