// Deterministic references into the seeded emulator dataset (backend/seed/users.py +
// backend/seed/builder.py). The e2e harness seeds the emulator ONCE via `seed_demo`, so
// every spec runs against the same shared, mutating dataset; the critical-path specs
// therefore target DISTINCT accounts/exceptions and pick their contribution/exception
// dynamically, so a CI retry (which re-runs against already-mutated state) still passes.
//
// NOT a test file (no `.spec`/`.test` suffix) — imported by the specs + helpers.

/** Shared demo password for all seeded accounts (backend/seed/users.py DEFAULT_PASSWORD). */
export const DEMO_PASSWORD = "DemoPass!234";

/** The three pinned demo accounts, one per role (backend/seed/users.py DEMO_USERS). */
export const USERS = {
  ops: "ops@demo.test", // OPERATIONS_USER
  mgr: "mgr@demo.test", // SERVICING_MANAGER
  admin: "admin@demo.test", // ADMINISTRATOR
} as const;

// Seeded loan ids (builder.py writes `loan_{key}`). Each below is a healthy account with an
// ACTIVE benefit that is accepting payments and has scheduled future contributions — the
// state each flow needs. Kept disjoint across flows so the shared seed never couples tests.
export const LOANS = {
  jordanLee: "loan_jordan_lee", // Path A  — process a scheduled contribution
  miaAdams: "loan_mia_adams", // Flow 202 — process (forced 202 → real land)
  henryBaker: "loan_henry_baker", // Flow STALE_WRITE — suspend (simulated 409, no change)
  ellaNelson: "loan_ella_nelson", // Flow 403 — ops locked affordance + real server 403
} as const;

/** Borrower display names (builder.py `{first} {last}`) — the account-detail h1. */
export const BORROWER_NAMES = {
  jordanLee: "Jordan Lee",
  miaAdams: "Mia Adams",
  henryBaker: "Henry Baker",
  ellaNelson: "Ella Nelson",
} as const;
