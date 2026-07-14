import { describe, it, expect } from "vitest";
import { permitted, ROLE_RANK } from "@/lib/permissions";
import type { Role } from "@/lib/types";

const ROLES: Role[] = ["OPERATIONS_USER", "SERVICING_MANAGER", "ADMINISTRATOR"];

describe("permitted (UX affordance gate)", () => {
  it("denies a null / undefined role (signed out or claim not yet loaded)", () => {
    expect(permitted(null, "OPERATIONS_USER")).toBe(false);
    expect(permitted(undefined, "ADMINISTRATOR")).toBe(false);
  });

  it("permits iff rank(role) >= rank(requires) — every role × requirement pair", () => {
    for (const role of ROLES) {
      for (const requires of ROLES) {
        expect(permitted(role, requires)).toBe(ROLE_RANK[role] >= ROLE_RANK[requires]);
      }
    }
  });

  it("keeps a strictly monotonic ladder ops < manager < admin", () => {
    expect(ROLE_RANK.OPERATIONS_USER).toBeLessThan(ROLE_RANK.SERVICING_MANAGER);
    expect(ROLE_RANK.SERVICING_MANAGER).toBeLessThan(ROLE_RANK.ADMINISTRATOR);
  });

  it("a manager clears ops-level work but not admin-level", () => {
    expect(permitted("SERVICING_MANAGER", "OPERATIONS_USER")).toBe(true);
    expect(permitted("SERVICING_MANAGER", "SERVICING_MANAGER")).toBe(true);
    expect(permitted("SERVICING_MANAGER", "ADMINISTRATOR")).toBe(false);
  });
});
