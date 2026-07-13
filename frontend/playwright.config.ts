// Playwright config for the critical-path E2E suite (specs/17 §17.4). Chromium only, run
// serially against the shared, single-seeded emulator dataset (Auth + Firestore) plus the
// Django command API. The CI `e2e` job (.github/workflows/ci.yml) seeds the emulator and
// starts Django + Next.js inside `infrastructure/scripts/e2e.sh`, then runs `playwright test`
// (this config); the `webServer` below only STARTS the Next.js dev server when one is not
// already up (reuseExistingServer), so it works both in that harness and for a local
// `npm run test:e2e` against a developer's running emulator + Django.
//
// Base URL / port are overridable (PLAYWRIGHT_BASE_URL / PLAYWRIGHT_PORT) so the harness can
// point at whatever host it serves the frontend on.

import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PLAYWRIGHT_PORT ?? "3000";
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  // The specs mutate a single shared emulator dataset (distinct targets per flow), so they
  // must not run concurrently.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Generous ceilings tolerate Next.js dev-mode on-demand compilation on a cold CI machine;
  // the specs normally finish in seconds.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 180_000,
    // Applied only when Playwright itself starts the dev server (ignored when an
    // already-running server is reused). Emulator-wired + the correct API base (NOTE: the
    // command client appends `/api/v1`, so the base must NOT include it).
    env: {
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: "demo-benefitservicing-workbench",
      NEXT_PUBLIC_FIREBASE_API_KEY: "demo-api-key",
      NEXT_PUBLIC_USE_FIREBASE_EMULATOR: "true",
      NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST: "http://localhost:9099",
      NEXT_PUBLIC_FIRESTORE_EMULATOR_HOST: "localhost:8080",
      NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
    },
  },
});
