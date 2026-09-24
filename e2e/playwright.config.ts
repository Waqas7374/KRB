import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real running stack (`make up`): the Vite dev
 * server, the API, PostgreSQL, the Celery worker and Mailpit. Nothing is
 * mocked — the point is to prove the browser, the API and the database agree.
 *
 * Run: `make e2e` (or `npx playwright test` in this directory).
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  // Tests share one database; running them in parallel would make the
  // Mailpit and audit-log assertions race each other.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
