import { expect, test } from "@playwright/test";

import { signIn, USERS } from "./helpers";

/** Every list and form screen, opened as the super-admin against the seeded data. */
const SCREENS: [path: string, heading: string | RegExp][] = [
  ["/", /^Welcome,/],
  ["/projects", "Projects"],
  ["/projects/new", "New project"],
  ["/sites", "Sites"],
  ["/sites/new", "New site"],
  ["/departments", "Departments"],
  ["/cost-centers", "Cost centres"],
  ["/vendors", "Vendors"],
  ["/vendors/new", "New vendor"],
  ["/materials", "Materials"],
  ["/materials/new", "New material"],
  ["/material-categories", "Material categories"],
  ["/units", "Units of measure"],
  ["/unit-conversions", "Unit conversions"],
  ["/calibration", "Weighbridge calibration"],
  ["/truck-types", "Truck types"],
  ["/warehouses", "Warehouses"],
  ["/users", "Users"],
  ["/roles", "Roles"],
  ["/approvals", "Approvals"],
  ["/approval-workflows", "Approval workflows"],
  ["/purchase-requests", "Purchase requests"],
  ["/purchase-requests/new", "New purchase request"],
  ["/rfqs", "RFQs"],
  ["/rfqs/new", "New RFQ"],
  ["/purchase-orders", "Purchase orders"],
  ["/purchase-orders/new", "New purchase order"],
  ["/business-rules", "Business rules"],
  ["/vendor-rates", "Vendor rates"],
  ["/deliveries", "Deliveries"],
  ["/deliveries/new", "Record a delivery"],
  ["/deliveries/review", "Delivery review"],
  ["/account", "My account"],
  ["/status", "KRB ERP"],
];

test("every screen renders without a crash or a failed load", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));

  await signIn(page, USERS.admin);
  for (const [path, heading] of SCREENS) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading, exact: typeof heading === "string" }).first(), path).toBeVisible();
    await expect(page.getByText("Loading…")).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByText(/could not be displayed|Could not load this data/), path).toHaveCount(0);
  }
  expect(pageErrors).toEqual([]);
});

test("detail pages open from their lists", async ({ page }) => {
  await signIn(page, USERS.admin);
  for (const [list, detailPattern] of [
    ["/projects", /\/projects\/[0-9a-f-]{36}$/],
    ["/sites", /\/sites\/[0-9a-f-]{36}$/],
    ["/vendors", /\/vendors\/[0-9a-f-]{36}$/],
    ["/materials", /\/materials\/[0-9a-f-]{36}$/],
    ["/users", /\/users\/[0-9a-f-]{36}$/],
    ["/roles", /\/roles\/[0-9a-f-]{36}$/],
  ] as const) {
    await page.goto(list);
    // A loaded row (skeleton rows have no link).
    const firstRow = page.locator("tbody tr").filter({ has: page.getByRole("link") }).first();
    await expect(firstRow).toBeVisible();
    await firstRow.focus();
    await page.keyboard.press("Enter"); // keyboard path, docs/08 §3
    await expect(page, list).toHaveURL(detailPattern);
    await expect(page.getByText(/could not be displayed|Could not load this data/)).toHaveCount(0);
  }
});
