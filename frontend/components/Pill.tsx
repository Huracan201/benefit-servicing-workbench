// StatusPill — the primary status idiom (specs/15 §15.1, U1 design). 12% tint,
// 26% inset ring, a currentColor dot, and a MANDATORY text label so status survives
// color-blindness / grayscale. Color/label are resolved through the shared statusMeta
// map unless overridden. Not a live region — the visible text is the accessible name.

import type { ReactNode } from "react";
import { pillClasses, statusMeta, type ColorToken } from "@/components/statusMeta";

export interface StatusPillProps {
  /** Status enum value; also selects the reserved token/label unless overridden. */
  status: string;
  /** Override the label text (still mandatory — defaults to the mapped label). */
  label?: ReactNode;
  /** Override the color token; otherwise derived from `status`. */
  token?: ColorToken;
  /** Hide the leading dot (rarely needed). */
  hideDot?: boolean;
  className?: string;
}

export function StatusPill({
  status,
  label,
  token,
  hideDot = false,
  className,
}: StatusPillProps) {
  const meta = statusMeta(status);
  const resolvedToken = token ?? meta.token;
  const text = label ?? meta.label;
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-pill px-2.5 py-0.5",
        "text-xs font-semibold leading-tight ring-1 ring-inset",
        pillClasses(resolvedToken),
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {hideDot ? null : (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-current"
        />
      )}
      {text}
    </span>
  );
}

export default StatusPill;
