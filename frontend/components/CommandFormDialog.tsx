"use client";

// CommandFormDialog — the self-driving modal for command actions that need a small typed
// body (a reason, a note, a resolution, an employment status). It MIRRORS the part-1
// ConfirmDialog shell EXACTLY — focus capture/restore keyed on open only, the Tab
// focus-trap with a panel fallback boundary, ESC-to-cancel, overlay-click-to-cancel, and
// the role="dialog"/aria-modal/aria-labelledby wiring (the focus-trap fixes part-1
// landed) — and adds controlled inputs plus required-field validation that keeps the
// submit action DISABLED until every required field is filled. It is mirrored rather than
// composed because ConfirmDialog can't express a validation-gated submit button; the
// field <input>/<select>/<textarea> controls are naturally picked up by the same trap.
//
// Phase lifecycle (specs/08): it stays mounted across confirming → submitting → error so
// (a) the submit button shows its in-flight spinner and (b) a retry after a transport
// error re-calls handle.submit() with the SAME frozen Idempotency-Key rather than
// re-arming a fresh intent (a new key could replay a mutation the server already
// accepted). It closes on settled/awaiting/idle, where the screen's live Firestore SOURCE
// subscription reflects the landed state — never a projection (specs/05 §5.7).

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Button from "@/components/Button";
import type { CommandHandle } from "@/hooks/useCommand";

export type FieldType = "text" | "textarea" | "select";

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldSpec {
  /** Body property this control writes (must match the command's request shape). */
  name: string;
  /** Visible + accessible label. */
  label: string;
  type: FieldType;
  /** Options for a `select` field. */
  options?: FieldOption[];
  /** Required fields must be non-empty before the submit button is enabled. */
  required?: boolean;
  placeholder?: string;
}

export interface CommandFormDialogProps {
  handle: CommandHandle;
  fields: FieldSpec[];
  /** Dialog heading (default: `handle.meta.label`). */
  title?: ReactNode;
  /** Submit button label (default: `handle.meta.label`). */
  submitLabel?: string;
}

// The exact focusable-selector the part-1 ConfirmDialog trap uses (mirrored verbatim).
const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

function blankValues(fields: FieldSpec[]): Record<string, string> {
  const blank: Record<string, string> = {};
  for (const f of fields) blank[f.name] = "";
  return blank;
}

const CONTROL_CLASS =
  "w-full rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-body text-ink " +
  "placeholder:text-ink-3 focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-1 focus-visible:outline-accent";

export function CommandFormDialog({
  handle,
  fields,
  title,
  submitLabel,
}: CommandFormDialogProps) {
  const { phase, meta, error } = handle;

  // Open across the whole confirm→submit→error interaction (see header).
  const open = phase === "confirming" || phase === "submitting" || phase === "error";
  const loading = phase === "submitting";

  const [values, setValues] = useState<Record<string, string>>(() => blankValues(fields));

  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);
  const titleId = useId();
  const baseFieldId = useId();
  const errorId = `${titleId}-error`;

  // Latest values for the stable keydown handler so it never re-binds mid-command.
  const loadingRef = useRef(loading);
  const cancelRef = useRef(handle.cancel);
  useEffect(() => {
    loadingRef.current = loading;
    cancelRef.current = handle.cancel;
  });

  // Reset the form to blank ONLY on a fresh open (false → true); never on the
  // submitting/error transitions, so a retry preserves the operator's input.
  useEffect(() => {
    if (open && !wasOpenRef.current) setValues(blankValues(fields));
    wasOpenRef.current = open;
  }, [open, fields]);

  // Focus capture + restore, keyed on `open` ONLY (mirrors ConfirmDialog): a `loading`
  // change can never fire the cleanup that restores focus to the trigger.
  useEffect(() => {
    if (!open) return;
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const raf = requestAnimationFrame(() => panelRef.current?.focus());
    return () => {
      cancelAnimationFrame(raf);
      // Restore focus to the trigger only if it is still in the document — after a command
      // settles, its row/button may have re-rendered or unmounted, and focusing a detached
      // node silently drops focus to <body>. Fall back to the main content region instead.
      const prev = previouslyFocused.current;
      if (prev && prev.isConnected) {
        prev.focus();
      } else {
        document.querySelector<HTMLElement>("main")?.focus?.();
      }
    };
  }, [open]);

  // ESC-to-cancel + Tab trap (mirrors ConfirmDialog exactly, incl. the panel fallback so
  // Tab keeps cycling inside the dialog even when the submit button is disabled).
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (!loadingRef.current) cancelRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      const active = document.activeElement as HTMLElement | null;
      if (focusable.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
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

  if (!open || fields.length === 0) return null;

  const missingRequired = fields.some(
    (f) => f.required === true && (values[f.name] ?? "").trim() === "",
  );

  const setField = (name: string, value: string) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  const onSubmit = () => {
    if (missingRequired || loading) return;
    // Emit only the non-empty fields; the invoker casts this to the command's typed
    // request shape (commandActions.ts), whose property names are the field `name`s.
    const body: Record<string, string> = {};
    for (const f of fields) {
      const raw = values[f.name] ?? "";
      if (raw.trim() !== "") body[f.name] = raw;
    }
    void handle.submit(body);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onMouseDown={() => {
        if (!loading) handle.cancel();
      }}
    >
      <div aria-hidden="true" className="absolute inset-0 bg-ink/40" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={phase === "error" && error != null ? errorId : undefined}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
        className="relative w-full max-w-md rounded border border-border bg-surface p-5 shadow-elevation outline-none"
      >
        <h2 id={titleId} className="font-display text-h1 font-semibold text-ink">
          {title ?? meta.label}
        </h2>

        <div className="mt-4 space-y-3">
          {fields.map((f) => {
            const fieldId = `${baseFieldId}-${f.name}`;
            const value = values[f.name] ?? "";
            const required = f.required === true;
            return (
              <div key={f.name} className="space-y-1">
                <label
                  htmlFor={fieldId}
                  className="block text-xs font-medium uppercase tracking-wide text-ink-3"
                >
                  {f.label}
                  {required ? (
                    <span className="text-critical"> *</span>
                  ) : null}
                </label>
                {f.type === "textarea" ? (
                  <textarea
                    id={fieldId}
                    value={value}
                    placeholder={f.placeholder}
                    required={required}
                    aria-required={required || undefined}
                    rows={4}
                    onChange={(e) => setField(f.name, e.target.value)}
                    className={`${CONTROL_CLASS} resize-y`}
                  />
                ) : f.type === "select" ? (
                  <select
                    id={fieldId}
                    value={value}
                    required={required}
                    aria-required={required || undefined}
                    onChange={(e) => setField(f.name, e.target.value)}
                    className={CONTROL_CLASS}
                  >
                    <option value="" disabled={required}>
                      {f.placeholder ?? "Select…"}
                    </option>
                    {(f.options ?? []).map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={fieldId}
                    type="text"
                    value={value}
                    placeholder={f.placeholder}
                    required={required}
                    aria-required={required || undefined}
                    onChange={(e) => setField(f.name, e.target.value)}
                    className={CONTROL_CLASS}
                  />
                )}
              </div>
            );
          })}
        </div>

        {phase === "error" && error != null ? (
          // No role="alert": the error is already announced assertively by the toast that
          // useCommand pushes; a second live region would read the identical message twice.
          // It stays associated to the dialog via aria-describedby (errorId) above.
          <p id={errorId} className="mt-3 text-body text-critical">
            {error.userMessage}
          </p>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => handle.cancel()} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant={meta.tone === "danger" ? "danger" : "primary"}
            loading={loading}
            disabled={missingRequired}
            onClick={onSubmit}
          >
            {submitLabel ?? meta.label}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default CommandFormDialog;
