"use client";

// CommandButton — the self-driving trigger for a single command action. It reads
// everything it needs from a `useCommand` handle (specs/08, specs/12): the label + tone
// from `meta`, the permission gate (a locked-but-focusable button with a "Requires
// <role>" tooltip — UX ONLY; Django still authorizes and a real 403 is surfaced as a
// toast by the handle), and the in-flight state (submitting/awaiting → spinner + inert).
// Clicking ALWAYS arms the intent first (mint + freeze one Idempotency-Key, specs/08);
// for a no-confirm action it then submits immediately, and for a confirm action the arm
// opens the paired <ConfirmAction> / <CommandFormDialog> that collects the decision/body.

import type { ReactNode } from "react";
import Button from "@/components/Button";
import type { CommandHandle } from "@/hooks/useCommand";

export type CommandButtonSize = "sm" | "md";

export interface CommandButtonProps {
  handle: CommandHandle;
  /** Overrides the default label (`handle.meta.label`). */
  children?: ReactNode;
  size?: CommandButtonSize;
  className?: string;
}

// `size="sm"` overrides the kit Button's fixed padding/text with `!important` utilities —
// equal-specificity Tailwind classes wouldn't reliably win by source order otherwise.
const SIZE_CLASS: Record<CommandButtonSize, string> = {
  sm: "!px-2 !py-1 !text-xs",
  md: "",
};

export function CommandButton({
  handle,
  children,
  size = "md",
  className,
}: CommandButtonProps) {
  const { permitted, meta, busy } = handle;
  const label: ReactNode = children ?? meta.label;
  const variant = meta.tone === "danger" ? "danger" : "primary";
  const composed =
    [SIZE_CLASS[size], className ?? ""].filter(Boolean).join(" ") || undefined;

  // Permission gate: a locked (focusable, tooltip'd) button, never wired to arm/submit.
  if (!permitted) {
    return (
      <Button
        variant={variant}
        locked
        lockedReason={`Requires ${meta.requires}`}
        className={composed}
      >
        {label}
      </Button>
    );
  }

  return (
    <Button
      variant={variant}
      loading={busy}
      disabled={busy}
      onClick={() => {
        // arm() mints + freezes the Idempotency-Key for this intent; a confirm action
        // then waits for the paired dialog, a no-confirm action submits straight away.
        handle.arm();
        if (!meta.confirm) void handle.submit();
      }}
      className={composed}
    >
      {label}
    </Button>
  );
}

export default CommandButton;
