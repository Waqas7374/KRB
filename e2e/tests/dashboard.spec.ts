import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn } from "./helpers";

/**
 * The delivery dashboard: today's loads appear in its tiles and tables, and every tile is a door
 * to the rows behind it. The suite shares the development database, so figures are asserted as
 * a change from a baseline read first.
 */
const STAFF = "staff.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const PROCUREMENT = "procurement@krb.example";

test("a load recorded today shows on the dashboard, and its tile opens the list", async ({
  browser,
  request,
}) => {
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto("/deliveries/dashboard");
  await expect(pm.getByRole("heading", { level: 1 })).toHaveText("Delivery dashboard");
  const tile = pm.getByRole("link", { name: /^Deliveries\s+\d+/ });
  await expect(tile).toBeVisible();
  const before = Number((await tile.innerText()).match(/\d+/)![0]);

  // A load with no order: it is saved, flagged, and waits for review.
  const token = await apiToken(request, STAFF);
  const site = sql(`select id from sites where code = 'GVH-S1'`)[0]!;
  const vendor = sql(`select id from vendors where status = 'ACTIVE' order by code limit 1`)[0]!;
  const material = sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const unit = sql(`select base_unit_id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const made = await request.post(`${API}/deliveries`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      site_id: site,
      vendor_id: vendor,
      truck_number: `E2E-${Date.now() % 100000}`,
      captured_at: new Date(Date.now() - 2 * 60_000).toISOString(),
      items: [{ material_id: material, unit_id: unit, quantity: "6" }],
    },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const delivery = (await made.json()) as { id: string; delivery_number: string };

  await pm.reload();
  await expect(pm.getByRole("link", { name: /^Deliveries\s+\d+/ })).toContainText(String(before + 1));
  // The "waiting for review" widget shows only the oldest few, so a brand-new entry need not
  // appear in it once the database holds real volume — that cap is checked on its own data in
  // the backend suite. What every tile must do is open the filtered list behind it, checked below.
  // The charts are paired with the table they summarise.
  await expect(pm.getByRole("table", { name: /Loads, tonnage and value for each day/ })).toBeVisible();

  // A tile is a door: "Open flags" lands on the filtered list.
  await pm.getByRole("link", { name: /^Open flags/ }).click();
  await expect(pm).toHaveURL(/has_open_flags=true/);
  await expect(pm.getByRole("heading", { level: 1 })).toHaveText("Deliveries");
  await expect(pm.getByRole("link", { name: delivery.delivery_number })).toBeVisible();
});

test("the rate overview shows what is in force and the road it took", async ({
  page,
  request,
}) => {
  // A vendor of our own with two periods: 100, then a 4 % rise (within the auto-approval band).
  const token = await apiToken(request, PROCUREMENT);
  const headers = { Authorization: `Bearer ${token}` };
  const created = await request.post(`${API}/vendors`, {
    headers,
    data: { legal_name: `E2E Overview Vendor ${Date.now()}`, ntn: uniqueNtn() },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const vendor = (await created.json()) as { id: string };
  const material = sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const unit = sql(`select base_unit_id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const day = (offset: number) => new Date(Date.now() + offset * 864e5).toISOString().slice(0, 10);
  for (const [rate, from] of [
    ["100", day(-60)],
    ["104", day(-10)],
  ] as const) {
    const made = await request.post(`${API}/vendor-rates`, {
      headers,
      data: {
        vendor_id: vendor.id,
        material_id: material,
        unit_id: unit,
        rate,
        effective_from: from,
        reason: "E2E",
      },
    });
    expect(made.ok(), await made.text()).toBeTruthy();
  }

  await signIn(page, PROCUREMENT);
  await page.goto(`/vendor-rates/grid?vendor_id=${vendor.id}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Rate overview");
  const row = page.locator("tbody tr").filter({ has: page.getByRole("link") }).first();
  await expect(row).toContainText("104.00");
  await expect(row).toContainText("+4.00%");
  const trend = row.getByRole("img", { name: /^Rate history:/ });
  await expect(trend).toBeVisible();
  // The line is decoration; the words carry the same fact, in order.
  await expect(trend).toHaveAttribute("aria-label", /100\.00.*, then .*104\.00/);
  await expect(row).toContainText("2 periods");
});
