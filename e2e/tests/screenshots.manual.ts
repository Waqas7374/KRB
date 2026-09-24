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
    ["projects", "/projects"],
    ["units", "/units"],
    ["roles", "/roles"],
  ] as const) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/${name}.png` });
  }
  await page.goto("/vendors");
  await page.locator("tbody tr a").first().click();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/vendor-detail.png`, fullPage: true });
  await page.goto("/projects");
  await page.locator("tbody tr a").first().click();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/project-detail.png`, fullPage: true });
  await page.goto("/vendors/new");
  await page.screenshot({ path: `${OUT}/vendor-form.png` });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/vendors");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${OUT}/vendors-dark.png` });
});
