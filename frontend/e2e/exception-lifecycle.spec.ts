// Path B — the exception lifecycle (specs/06 §6.4, specs/15 §15.3). Drive an OPEN
// operational exception through the operator workflow — Assign to me (status-neutral) →
// Mark in review (OPEN → IN_REVIEW) → Resolve (form; IN_REVIEW → RESOLVED) — asserting each
// status/assignee transition and that the exception leaves the OPEN queue. Every action is
// a real command through the typed client; the row state comes from the live SOURCE
// `operationalExceptions` subscription (never a projection — specs/05 §5.7).

import { test, expect } from "@playwright/test";
import { USERS } from "./seed";
import { signIn, exceptionsTable, exceptionRowByEntityId } from "./helpers";

test("Path B — assign, review, and resolve an open exception; it leaves the OPEN queue", async ({
  page,
}) => {
  await signIn(page, USERS.mgr);
  await page.goto("/exceptions");

  const table = exceptionsTable(page);
  // Wait for real rows (loading skeletons carry no action buttons).
  await expect(
    table.getByRole("button", { name: "Resolve" }).first(),
  ).toBeVisible({ timeout: 20_000 });

  // Operate on the first OPEN exception; capture its unique entity id so every later
  // assertion tracks THE SAME exception across tab switches (and a retry, running after a
  // prior resolve, simply picks another still-open one).
  const firstRow = table.locator("tbody tr").first();
  const entityId = (await firstRow.locator(".font-mono").first().innerText()).trim();
  expect(entityId.length).toBeGreaterThan(0);

  const row = exceptionRowByEntityId(page, entityId);

  // Assign to me — status-neutral: the assignee flips, the row stays OPEN.
  await row.getByRole("button", { name: "Assign to me" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Assign to me" }).click();
  await expect(row).toContainText("Assigned to me", { timeout: 20_000 });

  // Mark in review — OPEN → IN_REVIEW: the row leaves the OPEN queue.
  await row.getByRole("button", { name: "Review" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Mark in review" }).click();
  await expect(row).toHaveCount(0, { timeout: 20_000 });

  // The same exception is now on the IN_REVIEW tab, still assigned to me.
  await page.getByRole("tab", { name: "In review" }).click();
  await expect(row).toHaveCount(1, { timeout: 20_000 });
  await expect(row).toContainText("In review");
  await expect(row).toContainText("Assigned to me");

  // Resolve (requires a note) — IN_REVIEW → RESOLVED: leaves the IN_REVIEW queue.
  await row.getByRole("button", { name: "Resolve" }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("textbox", { name: "Resolution note" })
    .fill("Resolved via E2E critical-path test.");
  await dialog.getByRole("button", { name: "Resolve" }).click();
  await expect(row).toHaveCount(0, { timeout: 20_000 });

  // Landed as RESOLVED, and confirmed gone from the OPEN queue.
  await page.getByRole("tab", { name: "Resolved" }).click();
  await expect(row).toHaveCount(1, { timeout: 20_000 });
  await expect(row).toContainText("Resolved");

  await page.getByRole("tab", { name: "Open" }).click();
  await expect(row).toHaveCount(0, { timeout: 20_000 });
});
