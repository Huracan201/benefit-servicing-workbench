"use client";

// The single write-path engine every operator affordance runs through (specs/02 P1,
// specs/08, specs/12). It owns the arm → confirm → submit lifecycle, the idempotency-key
// discipline, the optimistic-concurrency header policy, the async 202/pending handling,
// and the toast feedback — so a screen only wires a button/dialog to this handle and then
// relies on its Firestore SOURCE subscription for the authoritative landed state (never a
// projection, specs/05 §5.7).
//
// Idempotency invariant (specs/08): `arm()` MINTS and FREEZES one Idempotency-Key for the
// whole intent. That same key is reused across the client's internal 202 poll AND across a
// user retry after a transport error — it is NEVER regenerated mid-intent, because a fresh
// key could replay a mutation the server may already have accepted (a double charge).
// `cancel()` (or a fresh `arm()`) discards the key and starts a new intent.
//
// Stale-write invariant (specs/08): the `If-Match` revision is likewise FROZEN at `arm()`
// (snapshotted from the live subscription value the operator was looking at). If a
// concurrent write advances the revision during the confirm window, submit still sends the
// armed value, so the server's precondition correctly fails with STALE_WRITE instead of
// silently succeeding against data the operator never saw.
//
// In-flight lock: `submit()` is ignored while a request is outstanding (`submitting`) or
// while an async op is still landing (`awaiting`), so a double-click cannot double-submit.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  COMMAND_ACTIONS,
  INVOKERS,
  type CommandActionKey,
  type CommandActionMeta,
} from "@/lib/commandActions";
import type { CommandCallOptions } from "@/lib/commandClient";
import { CommandError } from "@/lib/errors";
import { permitted } from "@/lib/permissions";
import type { Role } from "@/lib/types";
import { useToast } from "@/components/Toast";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type CommandPhase =
  | "idle"
  | "confirming"
  | "submitting"
  | "awaiting"
  | "settled"
  | "error";

export interface UseCommandArgs {
  /** Which registered command this affordance invokes. */
  action: CommandActionKey;
  /** The path id (agreementId / borrowerId / contributionId / exceptionId / loanId / uid). */
  id: string;
  /** The caller's role from the Firebase custom claim; null when signed out / not loaded. */
  role: Role | null;
  /**
   * The mutated entity's current `revision`; sent as `If-Match` ONLY for actions whose meta
   * has `usesIfMatch` (benefit suspend/resume/terminate, employment change). Ignored otherwise.
   * The value is snapshotted at `arm()`, so pass the live subscription revision each render.
   */
  expectedRevision?: number;
  /**
   * Called after a `settled` OR `pending` outcome. Use it to close a dialog / reset local
   * form state — NOT to refetch a projection; the live SOURCE subscription reflects the
   * landed state on its own (specs/05 §5.7).
   */
  onSettled?: () => void;
}

export interface CommandHandle {
  phase: CommandPhase;
  /** Local affordance gate (UX only — the server still authorizes; a 403 is still handled). */
  permitted: boolean;
  meta: CommandActionMeta;
  /** True while a request is outstanding or an async op is still landing. */
  busy: boolean;
  error: CommandError | null;
  /** idle → confirming: mint + freeze a fresh Idempotency-Key + the If-Match revision. */
  arm(): void;
  /** Back to idle: discard the frozen key (abandon the intent). */
  cancel(): void;
  /** confirming/error → submitting: dispatch with the frozen key; reused on retry. */
  submit(body?: unknown): Promise<void>;
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/** Mint a fresh idempotency key (specs/08). WebCrypto UUID with a safe non-crypto fallback. */
function freshIdempotencyKey(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useCommand(args: UseCommandArgs): CommandHandle {
  const { action, id, role, expectedRevision, onSettled } = args;
  const { push } = useToast();

  const meta = useMemo<CommandActionMeta>(() => COMMAND_ACTIONS[action], [action]);
  const allowed = permitted(role, meta.requires);

  const [phase, setPhaseState] = useState<CommandPhase>("idle");
  const [error, setError] = useState<CommandError | null>(null);

  // `phaseRef` mirrors `phase` synchronously so the guards below are race-free even before
  // React flushes the state update (a rapid double-click is blocked immediately).
  const phaseRef = useRef<CommandPhase>("idle");
  // The frozen Idempotency-Key for the current intent; null when disarmed.
  const keyRef = useRef<string | null>(null);
  // `liveRevisionRef` mirrors the latest `expectedRevision` prop every render; `arm()`
  // snapshots it into `frozenRevisionRef` so the If-Match sent at submit time is the revision
  // the operator was looking at when they armed — not one a concurrent write advanced during
  // the confirm window (which would silently defeat the stale-write guard).
  const liveRevisionRef = useRef<number | undefined>(expectedRevision);
  liveRevisionRef.current = expectedRevision;
  const frozenRevisionRef = useRef<number | undefined>(undefined);

  const setPhase = useCallback((next: CommandPhase) => {
    phaseRef.current = next;
    setPhaseState(next);
  }, []);

  const arm = useCallback(() => {
    // Don't start a new intent while a request is literally outstanding.
    if (phaseRef.current === "submitting") return;
    keyRef.current = freshIdempotencyKey();
    frozenRevisionRef.current = liveRevisionRef.current;
    setError(null);
    setPhase("confirming");
  }, [setPhase]);

  const cancel = useCallback(() => {
    // A client cancel can't abort an in-flight request; only reset from a resting phase.
    if (phaseRef.current === "submitting") return;
    keyRef.current = null;
    frozenRevisionRef.current = undefined;
    setError(null);
    setPhase("idle");
  }, [setPhase]);

  const submit = useCallback(
    async (body?: unknown) => {
      // In-flight lock: never dispatch while submitting or awaiting.
      if (phaseRef.current === "submitting" || phaseRef.current === "awaiting") return;

      // Normally `arm()` froze the key + revision; be defensive for a direct submit.
      if (keyRef.current == null) {
        keyRef.current = freshIdempotencyKey();
        frozenRevisionRef.current = liveRevisionRef.current;
      }
      const idempotencyKey = keyRef.current;

      setError(null);
      setPhase("submitting");

      const opts: CommandCallOptions = { idempotencyKey };
      if (role != null) opts.role = role;
      // Optimistic concurrency only where the endpoint accepts If-Match and we have a
      // revision. Use the revision FROZEN at arm() — not the live prop, which a concurrent
      // write may have advanced during the confirm window (specs/08 stale-write guard).
      const frozenRevision = frozenRevisionRef.current;
      if (meta.usesIfMatch && frozenRevision != null) {
        opts.expectedRevision = frozenRevision;
      }

      try {
        const outcome = await INVOKERS[action](id, body, opts);
        if (outcome.status === "completed") {
          setPhase("settled");
          // Generic completion acknowledgement; the business result (e.g. POSTED vs FAILED)
          // is rendered by the screen from its live SOURCE subscription, not from here.
          push({ title: meta.verb, tone: "good" });
        } else {
          // Async op still running: stay locked; the caller watches its subscription to see
          // the mutation land. `keyRef` is retained (the poll already reused it internally).
          setPhase("awaiting");
          push({
            title: "Submitted",
            description: "This is still processing and will update automatically.",
            tone: "info",
          });
        }
        onSettled?.();
      } catch (err) {
        const commandError =
          err instanceof CommandError
            ? err
            : new CommandError({
                code: "UNKNOWN",
                serverMessage: err instanceof Error ? err.message : null,
              });
        setError(commandError);
        setPhase("error");
        // Retriable? The frozen key is deliberately kept, so a user retry re-submits the
        // SAME intent (same key) rather than minting a new one (specs/08).
        push({
          title: commandError.userMessage,
          description: commandError.correlationId
            ? `Reference: ${commandError.correlationId}`
            : undefined,
          tone: "critical",
        });
      }
    },
    [action, id, role, meta, push, onSettled, setPhase],
  );

  // When the target action/entity changes, abandon any prior intent so the affordance stays
  // reusable — an 'awaiting' (pending-202) or 'settled'/'error' handle otherwise has no exit
  // and would stay busy/disabled forever. Never clobber a literally in-flight 'submitting'.
  useEffect(() => {
    if (phaseRef.current === "submitting") return;
    keyRef.current = null;
    frozenRevisionRef.current = undefined;
    setError(null);
    setPhase("idle");
  }, [action, id, setPhase]);

  const busy = phase === "submitting" || phase === "awaiting";

  return useMemo<CommandHandle>(
    () => ({ phase, permitted: allowed, meta, busy, error, arm, cancel, submit }),
    [phase, allowed, meta, busy, error, arm, cancel, submit],
  );
}

export default useCommand;
