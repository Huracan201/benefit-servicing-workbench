// frontend/scripts/capture-screenshots.mjs — capture the README/demo screenshots against a
// running local stack (emulator + Django + Next on :3000). Brought up by
// infrastructure/scripts/screenshots.sh. Standalone (NOT a Playwright *.spec) so it never runs
// in the CI e2e suite. Sign-in mirrors frontend/e2e/helpers.ts::signIn.
import { chromium } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { mkdir } from "node:fs/promises";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../../docs/img");
const BASE = process.env.SCREENSHOT_BASE_URL ?? "http://localhost:3000";
const EMAIL = process.env.SCREENSHOT_USER ?? "mgr@demo.test";
const PASSWORD = process.env.SEED_DEMO_PASSWORD ?? "DemoPass!234";

// [file, url, optional heading to await before shooting]
const SHOTS = [
  ["dashboard", "/", { name: "Portfolio dashboard" }],
  ["loan-portfolio", "/loans", null],
  ["loan-detail", "/loans/loan_jordan_lee", { name: "Jordan Lee" }],
  ["exceptions", "/exceptions", null],
];

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  await page.goto(`${BASE}/signin`);
  await page.locator('input[name="email"]').fill(EMAIL);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Portfolio dashboard" }).waitFor({ timeout: 30_000 });

  for (const [name, path, heading] of SHOTS) {
    await page.goto(`${BASE}${path}`);
    if (heading) {
      await page.getByRole("heading", heading).first().waitFor({ timeout: 30_000 });
    }
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(1200); // let inline-SVG charts + subscriptions settle
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
    console.log(`[screenshots] captured ${name}.png`);
  }

  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
