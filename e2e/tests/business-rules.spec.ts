import { expect, test } from "@playwright/test";

import { signIn, sql, USERS } from "./helpers";

/**
 * Business rules through the browser: an administrator creates rules, asks
 * which one applies, and sets an approval limit that stops a project manager
 * approving more than their limit — while leaving them able to send it back.
 *
 * The suite shares the development database, so every rule created here is
 * deactivated at the end, whether or not the test passed.
 */
const REQUESTER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const NAME = `E2E limit ${Date.now()}`;

test.afterAll(() => {
  sql(`update business_rules set is_active = false where name like 'E2E %'`);
});

test("the seeded defaults are listed and the resolver explains itself", async ({ page }) => {
  await signIn(page, USERS.admin);
  await page.goto("/business-rules");
  await expect(page.getByRole("heading", { name: "Business rules" })).toBeVisible();
  await expect(page.getByText("Default geofence radius")).toBeVisible();
  await expect(page.getByText("500 m").first()).toBeVisible();

  await page.getByRole("button", { name: "Which rule applies?" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Rule type").selectOption({ label: "Geofence radius" });
  await dialog.getByRole("button", { name: "Check" }).click();
  await expect(dialog.getByText("500 m")).toBeVisible();
  await expect(dialog.getByText("Used", { exact: true })).toBeVisible();
});

test("a project manager over their approval limit is told so and cannot approve", async ({
  browser,
}) => {
  // 1. The administrator sets a 100,000 limit for the project-manager role.
  const admin = await browser.newPage();
  await signIn(admin, USERS.admin);
  await admin.goto("/business-rules");
  await admin.getByRole("button", { name: "New rule" }).click();
  const dialog = admin.getByRole("dialog");
  await dialog.getByLabel("Rule type").selectOption({ label: "Approval limit" });
  await dialog.getByLabel("Name").fill(NAME);
  await dialog.getByLabel("Role").selectOption({ label: "Project Manager" });
  await dialog.getByLabel("limit", { exact: true }).fill("100000");
  await dialog.getByRole("button", { name: "Create rule" }).click();
  await expect(admin.getByText(NAME)).toBeVisible();
  await expect(admin.getByText("up to").first()).toBeVisible();
  expect(sql(`select count(*) from business_rules where name = '${NAME}' and is_active`)[0]).toBe("1");

  // 2. A 500,000 request lands with the project manager (step 1).
  const requester = await browser.newPage();
  await signIn(requester, REQUESTER);
  await requester.goto("/purchase-requests/new");
  await requester.getByLabel("Justification").fill(`E2E limit check ${Date.now()}`);
  await requester.getByLabel("Line 1 material").selectOption({ index: 1 });
  await requester.getByLabel("Quantity").fill("1000");
  await requester.getByLabel("Estimated rate").fill("500");
  await requester.getByRole("button", { name: /Save and submit/ }).click();
  await expect(requester.getByText("Pending approval").first()).toBeVisible();
  const number = (await requester.getByRole("heading", { level: 1 }).innerText()).trim();

  // 3. The project manager sees why, has no Approve button, and can still send it back.
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto("/approvals");
  await pm.getByRole("link", { name: number }).click();
  await expect(pm.getByText(/above your approval limit/)).toBeVisible();
  await expect(pm.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);
  await expect(pm.getByRole("button", { name: "Request changes" })).toBeVisible();
  await expect(pm.getByRole("button", { name: "Reject" })).toBeVisible();
});
