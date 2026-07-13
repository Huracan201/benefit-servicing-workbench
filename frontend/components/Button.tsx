"use client";

// Button — primary / danger / ghost variants plus a "locked-disabled" affordance for
// permission-gated commands (specs/12 / specs/15 §15.2: the frontend only surfaces
// affordances; Django enforces). A locked button stays focusable (aria-disabled, not
// the `disabled` attribute) so its tooltip reason is discoverable by hover AND
// keyboard, while onClick is suppressed. `loading` shows a spinner (motion-reduce
// safe) and blocks activation. Forwards its ref to the inner <button> (used e.g. by
// ConfirmDialog to move focus to the primary action).

import { forwardRef, useId, type ButtonHTMLAttributes, type ReactNode } from "react";

export type ButtonVariant = "primary" | "danger" | "ghost";

export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  variant?: ButtonVariant;
  /** Permission lock: renders disabled-looking but focusable, with a tooltip. */
  locked?: boolean;
  /** Tooltip / accessible reason shown when `locked`. */
  lockedReason?: string;
  /** Show a spinner and block activation. */
  loading?: boolean;
  children: ReactNode;
  className?: string;
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-ink hover:brightness-110 border border-transparent",
  danger: "bg-critical text-white hover:brightness-110 border border-transparent",
  ghost:
    "bg-surface-2 text-ink-2 hover:text-ink border border-border hover:border-accent/[0.4]",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    locked = false,
    lockedReason,
    loading = false,
    children,
    className,
    onClick,
    type = "button",
    disabled,
    ...rest
  },
  ref,
) {
  // Stable id linking the button to its lockedReason tooltip so AT announces WHY it's
  // disabled (the tooltip is otherwise visual-only, surfaced on hover/focus).
  const lockedReasonId = useId();
  // `inert` blocks activation for any reason; `hardDisabled` is the real attribute
  // (locked stays focusable so its tooltip is reachable by hover + keyboard).
  const inert = locked || loading || disabled === true;
  const hardDisabled = loading || disabled === true;
  return (
    <span className="group relative inline-flex">
      <button
        {...rest}
        ref={ref}
        type={type}
        aria-disabled={inert || undefined}
        aria-busy={loading || undefined}
        aria-describedby={locked && lockedReason ? lockedReasonId : rest["aria-describedby"]}
        disabled={hardDisabled}
        onClick={(e) => {
          if (inert) {
            e.preventDefault();
            return;
          }
          onClick?.(e);
        }}
        className={[
          "inline-flex items-center justify-center gap-1.5 rounded-sm px-3 py-1.5 text-sm font-semibold transition-[filter,color,border-color]",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          VARIANTS[variant],
          inert ? "cursor-not-allowed opacity-60" : "",
          className ?? "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {loading ? (
          <svg
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="9"
              stroke="currentColor"
              strokeWidth="3"
            />
            <path
              className="opacity-90"
              d="M12 3a9 9 0 0 1 9 9"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinecap="round"
            />
          </svg>
        ) : locked ? (
          <span aria-hidden="true">🔒</span>
        ) : null}
        {children}
      </button>
      {locked && lockedReason ? (
        <span
          id={lockedReasonId}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 hidden -translate-x-1/2 whitespace-nowrap rounded-sm border border-border bg-surface px-2 py-1 text-xs text-ink-2 shadow-elevation group-hover:block group-focus-within:block"
        >
          {lockedReason}
        </span>
      ) : null}
    </span>
  );
});

Button.displayName = "Button";

export default Button;
