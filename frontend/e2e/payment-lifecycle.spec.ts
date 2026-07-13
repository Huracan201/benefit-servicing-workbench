// Path A — the payment lifecycle (specs/09, specs/15 §15.3). A SERVICING_MANAGER opens a
// loan detail and processes the next SCHEDULED contribution through the real confirm dialog
// → the typed command client. Truth is asserted ONLY from the transactionally-updated
// SOURCE docs the screen subscribes to — the contribution status, its attempts
// subcollection, and the agreement balances — never a projection (specs/05 §5.7).

import { test, expect } from "@playwright/test";
import { USERS, LOANS, BORROWER_NAMES } from "./seed";
import {
  signIn,
  firstScheduledInstallment,
  scheduleRowByInstallment,
  statTileValue,
} from "./helpers";

test("Path A — process a scheduled contribution; the source contribution, attempt, and balances reflect it", async ({
  page,
}) => {
  await signIn(page, USERS.mgr);
  await page.goto(`/loans/${LOANS.jordanLee}`);

  // The account resolved from its SOURCE loan/borrower docs.
  await expect(
    page.getByRole("heading", { name: BORROWER_NAMES.jordanLee }),
  ).toBeVisible();

  const installment = await firstScheduledInstallment(page);
  const targetRow = scheduleRowByInstallment(page, installment);
  await expect(targetRow).toContainText("Scheduled");

  // Capture the pre-command balance (SOURCE agreement.amountPaidCents, via the KPI tile).
  // Wait for the real value first so we never capture the loading "—" placeholder.
  const paidValue = statTileValue(page, "Amount paid");
  await expect(paidValue).not.toHaveText("—");
  const paidBefore = (await paidValue.innerText()).trim();

  // Process (confirm dialog) — a real business command through the typed client.
  await targetRow.getByRole("button", { name: "Process payment" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Process payment" }).click();

  // Generic completion acknowledgement (the POSTED vs FAILED business result is read from
  // the live source subscription below, not from this toast — specs/09 §9.1).
  await expect(page.getByText(/Payment processed/i)).toBeVisible({ timeout: 20_000 });

  // SOURCE contribution transition: SCHEDULED → POSTED (inline finalize; PROCESSING is a
  // transient the auto-retry tolerates).
  await expect(targetRow).toContainText("Posted", { timeout: 20_000 });

  // SOURCE attempt appeared: select the row → its attempts subcollection shows att_001.
  await targetRow
    .getByRole("button", { name: `Select installment ${installment}` })
    .click();
  const attempts = page.getByRole("table", {
    name: `Payment attempts for installment ${installment}`,
  });
  await expect(attempts).toContainText("att_001");
  await expect(attempts.getByText(/Succeeded|Started/)).toBeVisible();

  // Balances updated on the SOURCE agreement (amount paid moved off its pre-command value).
  await expect(paidValue).not.toHaveText(paidBefore);
});
