import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql } from "./helpers";

/**
 * Stock bought over the counter, from the bill in the storekeeper's hand: drafted with a bill
 * number and rates, a photo of the bill attached (straight to storage), printed, and posted by
 * the project manager, which is when stock moves. A photo is also attached to a delivery.
 *
 * The suite shares the development database, so the bill number is unique per run and stock is
 * asserted as a change from a baseline.
 */
const MANAGER = "sm.gvh1@krb.example";
const STAFF = "staff.gvh1@krb.example";
const PM = "pm.gvh@krb.example";

// A real 1x1 PNG: the API checks the file's first bytes against its declared type.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const crush = () => sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
const onHand = () =>
  Number(
    sql(
      `select coalesce(sum(b.quantity_on_hand), 0) from inventory_balances b
         join warehouses w on w.id = b.warehouse_id
        where w.code = 'GVH-S1-YARD' and b.material_id = '${crush()}'`,
    )[0],
  );

test("a counter purchase: bill, photo, print, post", async ({ browser }) => {
  const vendor = sql(`select id from vendors where status = 'ACTIVE' order by code limit 1`)[0]!;
  const yard = sql(`select id from warehouses where code = 'GVH-S1-YARD'`)[0]!;
  const bill = `BILL-E2E-${Date.now()}`;
  const start = onHand();

  // --- The site manager enters it from the bill -------------------------------------------
  const manager = await browser.newPage();
  await signIn(manager, MANAGER);
  await manager.goto("/grns");
  await manager.getByRole("link", { name: "Counter purchase" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("New counter purchase");
  await manager.getByLabel(/^Vendor/).selectOption(vendor);
  await manager.getByLabel(/^Bill or receipt number/).fill(bill);
  await manager.getByLabel(/^Received into/).selectOption(yard);
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Quantity").fill("4");
  await manager.getByLabel("Rate per unit").fill("90");
  await expect(manager.getByText("360.00")).toBeVisible(); // the bill total, as typed
  await manager.getByRole("button", { name: "Save draft" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("GRN-");
  const grnId = manager.url().split("/").pop()!;
  const grnNumber = (await manager.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(sql(`select status || '|' || coalesce(delivery_id::text, 'none') from grns where id = '${grnId}'`)[0]).toBe(
    "DRAFT|none",
  );
  expect(onHand()).toBe(start); // a draft moves nothing
  await expect(manager.getByText(`Counter purchase · bill ${bill}`)).toBeVisible();

  // --- The same bill cannot be entered twice ----------------------------------------------
  await manager.goto("/grns/new");
  await manager.getByLabel(/^Vendor/).selectOption(vendor);
  await manager.getByLabel(/^Bill or receipt number/).fill(bill);
  await manager.getByLabel(/^Received into/).selectOption(yard);
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Quantity").fill("1");
  await manager.getByLabel("Rate per unit").fill("90");
  await manager.getByRole("button", { name: "Save draft" }).click();
  await expect(manager.getByText(/has already been received/)).toBeVisible();

  // --- A photo of the bill goes straight to storage, and is checked afterwards -------------
  await manager.goto(`/grns/${grnId}`);
  await manager.getByLabel("Choose a file to attach").setInputFiles({
    name: "bill.png",
    mimeType: "image/png",
    buffer: PNG,
  });
  await expect(manager.getByText("bill.png")).toBeVisible();
  expect(
    sql(
      `select (uploaded_at is not null)::text || '|' || document_type from attachments
        where entity_id = '${grnId}' and entity_type = 'grn'`,
    ),
  ).toEqual(["true|BILL"]);

  // --- It prints ---------------------------------------------------------------------------
  const download = manager.waitForEvent("download");
  await manager.getByRole("button", { name: "Download PDF" }).click();
  expect((await download).suggestedFilename()).toBe(`${grnNumber}.pdf`);

  // --- The project manager posts it: that is when the stock moves -----------------------------
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto(`/grns/${grnId}`);
  await pm.getByRole("button", { name: "Post to stock" }).click();
  await pm.getByRole("dialog").getByRole("button", { name: `Post ${grnNumber}` }).click();
  await expect(pm.getByText("Posted", { exact: true }).first()).toBeVisible();
  expect(
    sql(
      `select txn_type || '|' || quantity_in::numeric(18,2) || '|' || unit_cost::numeric(18,2)
         from inventory_transactions where source_id = '${grnId}'`,
    ),
  ).toEqual(["GRN_IN|4.00|90.00"]);
  expect(onHand()).toBe(start + 4);
});

test("a photo is attached to a delivery", async ({ browser, request }) => {
  const token = await apiToken(request, STAFF);
  const site = sql(`select id from sites where code = 'GVH-S1'`)[0]!;
  const vendor = sql(`select id from vendors where status = 'ACTIVE' order by code limit 1`)[0]!;
  const unit = sql(`select base_unit_id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const made = await request.post(`${API}/deliveries`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      site_id: site,
      vendor_id: vendor,
      truck_number: `E2E-${Date.now() % 100000}`,
      captured_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      items: [{ material_id: crush(), unit_id: unit, quantity: "6" }],
    },
  });
  expect(made.ok(), await made.text()).toBeTruthy();
  const delivery = (await made.json()) as { id: string };

  const staff = await browser.newPage();
  await signIn(staff, STAFF);
  await staff.goto(`/deliveries/${delivery.id}`);
  await staff.getByLabel("What this file is").selectOption("CHALLAN");
  await staff.getByLabel("Choose a file to attach").setInputFiles({
    name: "challan.png",
    mimeType: "image/png",
    buffer: PNG,
  });
  await expect(staff.getByText("challan.png")).toBeVisible();
  expect(
    sql(
      `select document_type from attachments where entity_id = '${delivery.id}' and uploaded_at is not null`,
    ),
  ).toEqual(["CHALLAN"]);
});
