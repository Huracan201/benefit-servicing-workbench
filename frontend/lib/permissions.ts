// Role-rank gate for UI affordances (specs/12 §12.2). PURE — no React, no I/O.
//
// UX ONLY. This decides whether to SHOW or ENABLE an affordance for the caller's role.
// It is NEVER the security boundary: the Django command layer authorizes every write
// independently (specs/12 §12.5), and a real 403 FORBIDDEN is still surfaced as a typed
// error even if a button was hidden. A hidden/disabled control is a convenience, not a
// guarantee.
//
// This local ROLE_RANK is a compact 1/2/3 capability ladder used only by `permitted`.
// It is distinct from `types.ts` `ROLE_RANK` (10/20/30, the audit/sort ladder) but AGREES
// on ordering: OPERATIONS_USER < SERVICING_MANAGER < ADMINISTRATOR, each a strict superset
// of the previous for servicing actions.

import type { Role } from "@/lib/types";

/** Monotonic capability ladder; a higher rank is a strict superset of every lower one. */
export const ROLE_RANK: Record<Role, number> = {
  OPERATIONS_USER: 1,
  SERVICING_MANAGER: 2,
  ADMINISTRATOR: 3,
};

/**
 * True when `role` is at least as privileged as `requires`. A null/undefined role
 * (signed out, or a custom-claim role not yet loaded) is never permitted.
 */
export function permitted(role: Role | null | undefined, requires: Role): boolean {
  if (role == null) return false;
  return ROLE_RANK[role] >= ROLE_RANK[requires];
}
