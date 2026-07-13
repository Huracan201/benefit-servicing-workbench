// Flow STALE_WRITE — optimistic-concurrency conflict handling (specs/08). A benefit
// lifecycle command carries the agreement's current `revision` as `If-Match`; a concurrent
// edit makes it stale and the server answers 409 STALE_WRITE. This asserts the CLIENT
// contract: the typed STALE_WRITE toast surfaces and there is NO phantom (optimistic) state
// change — the SOURCE benefit stays ACTIVE because the screen only renders confirmed
// Firestore source state.
//
// The 409 is produced by the REAL backend, which enforces `If-Match` server-side
// (commands.base.assert_expected_revision). The app genuinely sends the agreement's current
// revision as `If-Match` — we capture and assert it — then OVERWRITE it with a stale value on
// the way out so the server's optimistic-concurrency precondition genuinely rejects it. This
// exercises real server behavior + the client's typed-error → toast rendering, not a mock.

import { test, expect } from "@playwright/test";
import { USERS, LOANS, BORROWER_NAMES } from "./seed";
import { signIn, fulfillPreflight } from "./helpers";

test("Flow STALE_WRITE — a stale If-Match surfaces the typed 409 toast and makes no phantom change", async ({
  page,
}) => {
  await signIn(page, USERS.mgr);

  let capturedIfMatch: string | null = null;
  await page.route("**/api/v1/benefit-agreements/*/suspend", async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") {
      await fulfillPreflight(route);
      return;
    }
    // Capture the app's real (current-revision) If-Match, then send a stale one so the REAL
    // backend's assert_expected_revision produces a genuine 409 STALE_WRITE.
    capturedIfMatch = req.headers()["if-match"] ?? null;
    await route.continue({ headers: { ...req.headers(), "if-match": "999999" } });
  });

  await page.goto(`/loans/${LOANS.henryBaker}`);
  await expect(
    page.getByRole("heading", { name: BORROWER_NAMES.henryBaker }),
  ).toBeVisible();

  const benefitCard = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Benefit agreement" }) });
  await expect(benefitCard.getByText("Active", { exact: true })).toBeVisible();

  // Suspend (optional-reason form dialog) → submit; the interceptor answers 409.
  await benefitCard.getByRole("button", { name: "Suspend benefit" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Suspend benefit" }).click();

  // Typed STALE_WRITE toast (specs/08) — the operator is told to refresh & retry.
  await expect(page.getByText(/refresh and retry/i).first()).toBeVisible({
    timeout: 20_000,
  });

  // The app really did send optimistic-concurrency (a numeric revision as If-Match).
  expect(capturedIfMatch).not.toBeNull();
  expect(String(capturedIfMatch)).toMatch(/^\d+$/);

  // No phantom state change — the SOURCE benefit never flips to SUSPENDED; it stays ACTIVE.
  await expect(page.getByText("Suspended")).toHaveCount(0);
  await expect(benefitCard.getByText("Active", { exact: true })).toBeVisible();
});
