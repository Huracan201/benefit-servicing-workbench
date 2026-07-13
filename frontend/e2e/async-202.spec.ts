// Flow 202 ASYNC — the async completion protocol (specs/08 §8.3, specs/14). A command may
// answer 202 (still running); the client polls the SAME idempotency key while the UI shows
// an in-progress affordance, then the poll lands and the SOURCE subscription reflects the
// completed state — the return value is never trusted for state (specs/05 §5.7).
//
// The emulator runs follow-up tasks INLINE (always 200), so a natural 202 never occurs. We
// force it: the first process POST is answered 202 with a short Retry-After; the client's
// re-POST (identical request, same key) falls through to the REAL inline backend, which
// completes it → POSTED. This exercises the client's real 202-poll path AND the eventual
// source landing.

import { test, expect } from "@playwright/test";
import { USERS, LOANS, BORROWER_NAMES } from "./seed";
import {
  signIn,
  firstScheduledInstallment,
  scheduleRowByInstallment,
  corsJsonHeaders,
} from "./helpers";

test("Flow 202 — a 202 shows the in-progress affordance, then the source lands POSTED", async ({
  page,
}) => {
  await signIn(page, USERS.mgr);

  let postCount = 0;
  await page.route("**/api/v1/contributions/*/process", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") {
      // Let the CORS preflight reach the live backend (which allows the origin).
      await route.continue();
      return;
    }
    postCount += 1;
    if (postCount === 1) {
      // First response: 202 IN_PROGRESS + a short Retry-After. The client waits, then
      // re-POSTs the identical request with the SAME Idempotency-Key (specs/08 §8.3).
      await route.fulfill({
        status: 202,
        headers: { ...corsJsonHeaders(route), "Retry-After": "1" },
        body: JSON.stringify({
          error: {
            code: "IN_PROGRESS",
            message: "operation in progress",
            correlationId: "e2e-async",
          },
        }),
      });
    } else {
      // The poll re-POST reaches the real (inline) backend, which completes it → 200 POSTED.
      await route.continue();
    }
  });

  await page.goto(`/loans/${LOANS.miaAdams}`);
  await expect(
    page.getByRole("heading", { name: BORROWER_NAMES.miaAdams }),
  ).toBeVisible();

  const installment = await firstScheduledInstallment(page);
  const targetRow = scheduleRowByInstallment(page, installment);

  await targetRow.getByRole("button", { name: "Process payment" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Process payment" });
  await confirm.click();

  // In-progress affordance: the submit stays busy across the 202 poll wait (aria-busy). The
  // ≥1s Retry-After keeps the busy state observable for the auto-retrying assertion.
  await expect(confirm).toHaveAttribute("aria-busy", "true");

  // The poll landed: completion acknowledged, and the SOURCE contribution is POSTED.
  await expect(page.getByText(/Payment processed/i)).toBeVisible({ timeout: 20_000 });
  await expect(targetRow).toContainText("Posted", { timeout: 20_000 });

  // Proof the client actually polled after the 202 (initial POST + at least one re-POST).
  expect(postCount).toBeGreaterThanOrEqual(2);
});
