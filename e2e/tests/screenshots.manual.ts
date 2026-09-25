/**
 * Not part of the suite (the file name does not match *.spec.ts). Captures
 * key screens for a human layout review:
 *   npx playwright test --config=screenshots.config.ts
 */
import { test } from "@playwright/test";

import { signIn, USERS } from "./helpers";

const OUT = process.env.SHOT_DIR ?? "screenshots";

test("capture key screens", async ({ page }) => {
  await signIn(page, USERS.admin);
  for (const [name, path] of [
    ["home", "/"],
    ["vendors", "/vendors"],
    ["purchase-requests", "/purchase-requests"],
    ["approvals", "/approvals"],
    ["workflows", "/approval-workflows"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/${name}.png` });
  }
  await page.goto("/purchase-requests");
  await page.locator("tbody tr a").first().click();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/pr-detail.png`, fullPage: true });
  await page.goto("/purchase-requests/new");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/pr-form.png`, fullPage: true });
});
