// Flow 403 FORBIDDEN — the SERVER, not the button, is the authorization boundary
// (specs/12 §12.5). Signed in as an OPERATIONS_USER, a SERVICING_MANAGER-only action is
// LOCKED in the UI (affordance only). We then drive a manager-only endpoint PAST that lock:
// an ops-permitted note POST is rewritten onto the manager `suspend` endpoint, so the REAL
// Django role gate (RequireManager, which runs before any entity lookup) rejects the ops
// identity with 403 — and the app renders the typed FORBIDDEN toast from that real 403.

import { test, expect } from "@playwright/test";
import { USERS, LOANS, BORROWER_NAMES } from "./seed";
import { signIn } from "./helpers";

test("Flow 403 — a manager-only action is rejected by the server for an ops identity, past the locked button", async ({
  page,
}) => {
  await signIn(page, USERS.ops);
  await page.goto(`/loans/${LOANS.ellaNelson}`);
  await expect(
    page.getByRole("heading", { name: BORROWER_NAMES.ellaNelson }),
  ).toBeVisible();

  // The manager-only affordance is present but LOCKED (focusable, aria-disabled) — a hint,
  // never the boundary. The kit renders it as a locked (not hard-disabled) button.
  const benefitCard = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Benefit agreement" }) });
  const suspendBtn = benefitCard.getByRole("button", { name: "Suspend benefit" });
  await expect(suspendBtn).toBeVisible();
  await expect(suspendBtn).toHaveAttribute("aria-disabled", "true");

  // Rewrite the ops-permitted note POST onto the manager-only suspend endpoint. Django's
  // RequireManager rejects the ops token before touching Firestore, so the placeholder id is
  // irrelevant → a real 403. The OPTIONS preflight is passed through to the live backend.
  await page.route("**/api/v1/loans/*/notes", async (route) => {
    const req = route.request();
    if (req.method() === "OPTIONS") {
      await route.continue();
      return;
    }
    const suspendUrl = req
      .url()
      .replace(
        /\/loans\/[^/]+\/notes.*$/,
        "/benefit-agreements/e2e-forbidden-probe/suspend",
      );
    await route.continue({ url: suspendUrl });
  });

  // Drive the note action (ops IS permitted to add notes, so this is a genuine submit that
  // the interceptor redirects onto the forbidden endpoint).
  await page.getByRole("button", { name: "Add note" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Note" }).fill("E2E forbidden probe.");
  await dialog.getByRole("button", { name: "Add note" }).click();

  // The app surfaces the typed FORBIDDEN toast from the server's real 403.
  await expect(
    page.getByText(/don't have permission to do this/i),
  ).toBeVisible({ timeout: 20_000 });
});
