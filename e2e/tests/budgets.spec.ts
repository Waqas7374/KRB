import { expect, test } from "@playwright/test";

import { signIn, USERS } from "./helpers";

/**
 * A budget's own lifecycle through the browser: drafted with a line, approved,
 * revised (the original figure is kept alongside the new one), and closed.
 * The purchase-order commitment and goods-receipt release side of this slice
 * is covered at the API/database level (backend/tests/integration/test_
 * budgets.py) and, indirectly, by grn.spec.ts and counter-purchase.spec.ts —
 * both post a real GRN through the browser, which now also builds the journal
 * entry this slice adds.
 */
test("a budget is drafted, approved, revised and closed", async ({ page }) => {
  await signIn(page, USERS.finance);
  await page.goto("/finance/budgets/new");

  // One budget per project and fiscal year, ever — the shared dev database
  // keeps whatever a previous run created, so a fiscal year far in the
  // future (never a real one) keeps reruns from colliding with it.
  const name = `E2E budget ${Date.now()}`;
  const fiscalYear = String(2050 + (Date.now() % 40));
  await page
    .locator('[name="project_id"]')
    .selectOption({ label: "GVH — Green Valley Housing Project" });
  await page.locator('[name="fiscal_year"]').fill(fiscalYear);
  await page.locator('[name="name"]').fill(name);
  await page
    .locator('[name="lines.0.account_id"]')
    .selectOption({ label: "6200 — Contractor and Labour Cost" });
  await page.locator('[name="lines.0.budgeted_amount"]').fill("500000");
  await page.getByRole("button", { name: "Save draft" }).click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Revise" }).click();
  const revised = page.getByRole("dialog").getByLabel(/^6200/);
  await revised.fill("650000");
  await page.getByRole("dialog").getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByText("Revised", { exact: true }).first()).toBeVisible();
  // The line now shows both figures: the new one, and the original struck through.
  await expect(page.locator(".line-through").first()).toBeVisible();

  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Close budget" }).click();
  await expect(page.getByText("Closed", { exact: true }).first()).toBeVisible();
});
