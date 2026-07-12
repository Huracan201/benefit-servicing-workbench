// RoleGate — an AFFORDANCE-ONLY wrapper (specs/12 / specs/15 §15.2). It decides
// whether to show or lighten a control based on the viewer's role, but it is NOT a
// security boundary: Django authorizes every write and Firestore rules authorize
// reads. Never gate data exposure on this — only affordances.
//
// `requires` is the minimum role (by ROLE_RANK). When the viewer is under it:
//   mode="hide"    -> render `fallback` (default: nothing)
//   mode="fallback"-> same as hide but expects a provided fallback (e.g. a locked Button)

import type { ReactNode } from "react";
import { ROLE_RANK, type Role } from "@/lib/types";

export interface RoleGateProps {
  /** The viewer's role (from Firebase custom claims); null/undefined = no role. */
  role: Role | null | undefined;
  /** Minimum role required to see the affordance. */
  requires: Role;
  children: ReactNode;
  /** Rendered when not permitted (e.g. a locked Button). Default: nothing. */
  fallback?: ReactNode;
}

export function permitted(role: Role | null | undefined, requires: Role): boolean {
  if (!role) return false;
  return ROLE_RANK[role] >= ROLE_RANK[requires];
}

export function RoleGate({ role, requires, children, fallback = null }: RoleGateProps) {
  return <>{permitted(role, requires) ? children : fallback}</>;
}

export default RoleGate;
