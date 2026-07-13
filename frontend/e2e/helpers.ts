// Shared Playwright helpers for the critical-path specs. NOT a test file (no `.spec`/
// `.test` suffix) — imported by the specs.
//
// Conventions: assertions come from the app's OWN rendered UI, which the screens drive
// from their Firestore SOURCE subscriptions (never a projection — specs/05 §5.7). The
// tests never write Firestore or call the command API directly for state; they drive the
// real buttons/dialogs (the typed command client), exactly like an operator.

import { expect, type Page, type Locator, type Route } from "@playwright/test";
import { DEMO_PASSWORD } from "./seed";

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/**
 * Sign in through the real /signin screen against the Auth emulator, then wait for the
 * dashboard to confirm the session resolved. Firebase persists the session in the browser
 * context (IndexedDB), so later same-context `page.goto`s stay authed.
 */
export async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/signin");
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("heading", { name: "Portfolio dashboard" }),
  ).toBeVisible({ timeout: 30_000 });
}

// ---------------------------------------------------------------------------
// CORS-aware interception (the command API is cross-origin: :3000 → :8000)
// ---------------------------------------------------------------------------

function requestOrigin(route: Route): string {
  return route.request().headers()["origin"] ?? "*";
}

/**
 * Headers that make a `route.fulfill`ed cross-origin JSON response readable by the browser
 * (the command client's fetch is cross-origin, so a simulated response needs the ACAO +
 * exposed poll/correlation headers the real backend would send — config/settings.py CORS).
 */
export function corsJsonHeaders(route: Route): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": requestOrigin(route),
    "Access-Control-Expose-Headers": "Retry-After, X-Correlation-Id",
    Vary: "Origin",
  };
}

/** Answer a CORS preflight so a fulfilled-response flow needs no live backend for the OPTIONS. */
export async function fulfillPreflight(route: Route): Promise<void> {
  await route.fulfill({
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": requestOrigin(route),
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers":
        "authorization, content-type, idempotency-key, if-match, x-correlation-id",
      "Access-Control-Max-Age": "600",
      Vary: "Origin",
    },
  });
}

// ---------------------------------------------------------------------------
// Loan-detail: contribution schedule
// ---------------------------------------------------------------------------

/** The contribution-schedule DenseTable on the account-detail screen (by its caption). */
export function scheduleTable(page: Page): Locator {
  return page.getByRole("table", { name: "Contribution schedule" });
}

/** The schedule row for a specific installment number ("013"), located by its stable
 *  in-cell "Select installment NNN" button (present regardless of the row's status). */
export function scheduleRowByInstallment(page: Page, installment: string): Locator {
  return scheduleTable(page)
    .locator("tbody tr")
    .filter({
      has: page.getByRole("button", { name: `Select installment ${installment}` }),
    });
}

/**
 * The installment number ("013") of the first still-SCHEDULED row — the only rows that
 * expose a "Process payment" button on a healthy account. Dynamic on purpose: a CI retry
 * re-runs after the first installment already POSTED, so it simply targets the next one.
 */
export async function firstScheduledInstallment(page: Page): Promise<string> {
  const firstRow = scheduleTable(page)
    .locator("tbody tr")
    .filter({ has: page.getByRole("button", { name: "Process payment" }) })
    .first();
  await expect(firstRow).toBeVisible({ timeout: 20_000 });
  const label = await firstRow
    .getByRole("button", { name: /^Select installment / })
    .getAttribute("aria-label");
  return (label ?? "").replace("Select installment ", "").trim();
}

/**
 * The hero-value node of a KPI StatTile, located by its visible label. The kit ships no
 * test-ids and the value carries no ARIA role, so anchor on the exact label text and step
 * to the sibling value via the StatTile DOM structure (label sits in the header row; the
 * value is the header row's next sibling div).
 */
export function statTileValue(page: Page, label: string): Locator {
  return page
    .getByText(label, { exact: true })
    .locator("xpath=../following-sibling::div[1]");
}

// ---------------------------------------------------------------------------
// Exceptions workbench
// ---------------------------------------------------------------------------

/** The operational-exceptions DenseTable on the workbench (by its caption). */
export function exceptionsTable(page: Page): Locator {
  return page.getByRole("table", { name: "Operational exceptions" });
}

/** The exception row(s) whose text contains a specific (unique) entity id. */
export function exceptionRowByEntityId(page: Page, entityId: string): Locator {
  return exceptionsTable(page).locator("tbody tr").filter({ hasText: entityId });
}
