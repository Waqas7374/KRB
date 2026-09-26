import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn } from "./helpers";

/**
 * From the truck to the shelf, through the browser: a load is recorded, approved
 * and received; inspection turns part of it away; posting moves only the
 * accepted part into stock at its priced cost; cancelling reverses it with a
 * contra entry. PostgreSQL is asserted at each step.
 *
 * The suite shares the development database, so the vendor is created here and
 * given a rate of 100 per tonne; nothing else prices it.
 */
const STAFF = "staff.gvh1@krb.example";
const MANAGER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const PROCUREMENT = "procurement@krb.example";
const ADMIN = "admin@krb.example";

test("record, approve, receive, inspect, post and cancel", async ({ browser, request }) => {
  // --- A vendor of our own, priced at 100 per tonne of crush ------------------------------
  const token = await apiToken(request, PROCUREMENT);
  const headers = { Authorization: `Bearer ${token}` };
  const legalName = `E2E Stock Vendor ${Date.now()}`;
  const created = await request.post(`${API}/vendors`, {
    headers,
    data: { legal_name: legalName, ntn: uniqueNtn() },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const vendor = (await created.json()) as { id: string };
  const crush = sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const ton = sql(`select base_unit_id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const priced = await request.post(`${API}/vendor-rates`, {
    headers,
    data: {
      vendor_id: vendor.id,
      material_id: crush,
      unit_id: ton,
      rate: "100",
      effective_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10),
    },
  });
  expect(priced.ok(), await priced.text()).toBeTruthy();

  // --- Site staff record the load; the site manager approves it ---------------------------
  const staff = await browser.newPage();
  await signIn(staff, STAFF);
  await staff.goto("/deliveries/new");
  await staff.getByLabel("Site").selectOption({ index: 1 });
  await staff.getByLabel("Vendor").selectOption(vendor.id);
  await staff.getByLabel("Line 1 material").selectOption(crush);
  await staff.getByLabel("Quantity").fill("12.5");
  await staff.getByLabel("Truck number").fill(`E2E-${Date.now() % 100000}`);
  await staff.getByRole("button", { name: "Record delivery" }).click();
  await expect(staff.getByRole("heading", { level: 1 })).toContainText("DLV-");
  const deliveryId = staff.url().split("/").pop()!;

  const manager = await browser.newPage();
  await signIn(manager, MANAGER);
  await manager.goto(`/deliveries/${deliveryId}`);
  await manager.getByRole("button", { name: "Approve" }).click();
  await manager.getByRole("dialog").getByRole("button", { name: /^Approve DLV-/ }).click();
  await expect(manager.getByText("Approved", { exact: true }).first()).toBeVisible();

  // --- Receive into stock: a draft GRN, priced from the vendor's rate ---------------------
  await manager.getByRole("button", { name: "Receive into stock" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("GRN-");
  const grnId = manager.url().split("/").pop()!;
  const grnNumber = (await manager.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(sql(`select status from grns where id = '${grnId}'`)[0]).toBe("DRAFT");
  // Drafting moves no stock.
  expect(sql(`select count(*) from inventory_transactions where source_id = '${grnId}'`)[0]).toBe("0");

  // --- Inspection: 10 of 12.5 t taken, the rest turned away with a reason -----------------
  await manager.getByRole("button", { name: "Record inspection" }).click();
  const drawer = manager.getByRole("dialog");
  await drawer.getByLabel(/accepted quantity/).fill("10");
  await drawer.getByLabel(/rejection reason/).fill("Contaminated with clay");
  await drawer.getByRole("button", { name: "Save inspection" }).click();
  await expect(manager.getByText("Partial").first()).toBeVisible();
  await expect(manager.getByText("Rejected: Contaminated with clay")).toBeVisible();
  // The site manager may not see valuation, and cannot post.
  await expect(manager.getByText("Hidden").first()).toBeVisible();
  await expect(manager.getByRole("button", { name: "Post to stock" })).toHaveCount(0);

  // --- The project manager posts it: only the accepted 10 t go into stock ---------------
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto(`/grns/${grnId}`);
  await expect(pm.getByText("Not priced")).toHaveCount(0);
  await pm.getByRole("button", { name: "Post to stock" }).click();
  await pm.getByRole("dialog").getByRole("button", { name: `Post ${grnNumber}` }).click();
  await expect(pm.getByText("Posted", { exact: true }).first()).toBeVisible();

  expect(sql(`select status from grns where id = '${grnId}'`)[0]).toBe("POSTED");
  expect(
    sql(
      `select txn_type || '|' || quantity_in::numeric(18,2) || '|' || unit_cost::numeric(18,2)
         from inventory_transactions where source_id = '${grnId}'`,
    ),
  ).toEqual(["GRN_IN|10.00|100.00"]);
  expect(sql(`select status from deliveries where id = '${deliveryId}'`)[0]).toBe("PARTIALLY_RECEIVED");

  // The stock screen shows it, valued, for someone who may see valuation.
  await pm.goto("/inventory");
  await expect(pm.getByRole("link", { name: /Crush 12mm/ }).first()).toBeVisible();

  // --- Cancelling reverses it with a contra entry: nothing is deleted --------------------
  const admin = await browser.newPage();
  await signIn(admin, ADMIN);
  await admin.goto(`/grns/${grnId}`);
  await admin.getByRole("button", { name: "Cancel", exact: true }).click();
  const cancel = admin.getByRole("dialog");
  await cancel.getByLabel("Reason").fill("Posted against the wrong load");
  await cancel.getByRole("button", { name: `Cancel ${grnNumber}` }).click();
  await expect(admin.getByText("Cancelled", { exact: true }).first()).toBeVisible();

  expect(
    sql(
      `select txn_type from inventory_transactions where source_id = '${grnId}'
         or source_type = 'GRN_CANCEL' and source_id = '${grnId}' order by posted_at`,
    ),
  ).toEqual(["GRN_IN", "REVERSAL_OUT"]);
  expect(sql(`select status from deliveries where id = '${deliveryId}'`)[0]).toBe("APPROVED");
  expect(sql(`select grn_id is null from deliveries where id = '${deliveryId}'`)[0]).toBe("t");
});
