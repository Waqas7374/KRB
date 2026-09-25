/**
 * Not part of the suite (the file name does not match *.spec.ts). Captures
 * key screens for a human layout review:
 *   npx playwright test --config=screenshots.config.ts
 */
import { test } from "@playwright/test";

import { signIn, sql, USERS } from "./helpers";

const OUT = process.env.SHOT_DIR ?? "screenshots";

test("capture key screens", async ({ page }) => {
  await signIn(page, USERS.admin);
  for (const [name, path] of [
    ["home", "/"],
    ["vendors", "/vendors"],
    ["purchase-requests", "/purchase-requests"],
    ["approvals", "/approvals"],
    ["workflows", "/approval-workflows"],
    ["rfqs", "/rfqs"],
    ["purchase-orders", "/purchase-orders"],
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

  // The most recent RFQ with quotations, and the most recent order.
  const [rfq] = sql(
    "select r.id from rfqs r where exists (select 1 from vendor_quotations q where q.rfq_id = r.id) order by r.created_at desc limit 1",
  );
  if (rfq) {
    await page.goto(`/rfqs/${rfq}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/rfq-detail.png`, fullPage: true });
    await page.goto(`/rfqs/${rfq}/comparison`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/comparison.png`, fullPage: true });
    await page.goto(`/rfqs/${rfq}/quotations/new`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/quotation-form.png`, fullPage: true });
  }
  const [po] = sql("select id from purchase_orders order by created_at desc limit 1");
  if (po) {
    await page.goto(`/purchase-orders/${po}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/po-detail.png`, fullPage: true });
    await page.goto(`/purchase-orders/${po}/edit`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${OUT}/po-form.png`, fullPage: true });
  }
});
