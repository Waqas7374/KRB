import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn } from "./helpers";

/**
 * A vendor invoice through the browser: created against a real GRN line,
 * matched within tolerance, and approved — clearing the GRN's own accrual
 * into a real payable. The PO -> delivery -> GRN pipeline behind it is built
 * directly through the API (it is already exercised, end to end through the
 * browser, by grn.spec.ts and budgets.spec.ts); this spec is the one that is
 * new here: the invoice screens themselves.
 */
const ADMIN = "admin@krb.example";
const PROCUREMENT = "procurement@krb.example";
const STAFF = "staff.gvh1@krb.example";
const MANAGER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const FINANCE = "finance@krb.example";

test("a vendor invoice is created against a GRN, matched and approved", async ({
  browser,
  request,
}) => {
  const token = await apiToken(request, ADMIN);
  const headers = { Authorization: `Bearer ${token}` };
  const legalName = `E2E Invoice Vendor ${Date.now()}`;
  const vendorCreated = await request.post(`${API}/vendors`, {
    headers,
    data: { legal_name: legalName, ntn: uniqueNtn() },
  });
  expect(vendorCreated.ok(), await vendorCreated.text()).toBeTruthy();
  const vendor = (await vendorCreated.json()) as { id: string };
  const procurementToken = await apiToken(request, PROCUREMENT);
  const vendorApproved = await request.post(`${API}/vendors/${vendor.id}/approve`, {
    headers: { Authorization: `Bearer ${procurementToken}` },
    data: {},
  });
  expect(vendorApproved.ok(), await vendorApproved.text()).toBeTruthy();

  const crush = sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const ton = sql(`select base_unit_id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
  const project = sql(`select id from projects where code = 'GVH'`)[0]!;
  const site = sql(`select id from sites where code = 'GVH-S1'`)[0]!;
  const priced = await request.post(`${API}/vendor-rates`, {
    headers: { Authorization: `Bearer ${procurementToken}` },
    data: {
      vendor_id: vendor.id,
      material_id: crush,
      unit_id: ton,
      rate: "100",
      effective_from: new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10),
    },
  });
  expect(priced.ok(), await priced.text()).toBeTruthy();

  // --- A purchase order, approved -----------------------------------------------------
  const poCreated = await request.post(`${API}/purchase-orders`, {
    headers,
    data: {
      vendor_id: vendor.id,
      project_id: project,
      site_id: site,
      items: [{ material_id: crush, unit_id: ton, quantity: "20", rate: "100" }],
    },
  });
  expect(poCreated.ok(), await poCreated.text()).toBeTruthy();
  const po = (await poCreated.json()) as { id: string; items: { id: string }[] };
  const submitted = await request.post(`${API}/purchase-orders/${po.id}/submit`, { headers });
  expect(submitted.ok(), await submitted.text()).toBeTruthy();
  const { approval_request_id } = (await submitted.json()) as { approval_request_id: string };
  const approved = await request.post(`${API}/approvals/requests/${approval_request_id}/approve`, {
    headers: { Authorization: `Bearer ${procurementToken}` },
    data: {},
  });
  expect(approved.ok(), await approved.text()).toBeTruthy();

  // --- Delivered, approved and received in full ---------------------------------------
  const staffToken = await apiToken(request, STAFF);
  const deliveryCreated = await request.post(`${API}/deliveries`, {
    headers: { Authorization: `Bearer ${staffToken}` },
    data: {
      site_id: site,
      vendor_id: vendor.id,
      purchase_order_id: po.id,
      truck_number: `E2E-${Date.now() % 100000}`,
      captured_at: new Date().toISOString(),
      latitude: 31.411,
      longitude: 74.2461,
      gps_accuracy_m: 8,
      items: [{ material_id: crush, unit_id: ton, quantity: "20" }],
    },
  });
  expect(deliveryCreated.ok(), await deliveryCreated.text()).toBeTruthy();
  const delivery = (await deliveryCreated.json()) as { id: string };
  const managerToken = await apiToken(request, MANAGER);
  const managerHeaders = { Authorization: `Bearer ${managerToken}` };
  const deliveryApproved = await request.post(`${API}/deliveries/${delivery.id}/approve`, {
    headers: managerHeaders,
    data: { comments: "Checked on site" },
  });
  expect(deliveryApproved.ok(), await deliveryApproved.text()).toBeTruthy();
  const grnCreated = await request.post(`${API}/deliveries/${delivery.id}/convert-to-grn`, {
    headers: managerHeaders,
    data: {},
  });
  expect(grnCreated.ok(), await grnCreated.text()).toBeTruthy();
  const grn = (await grnCreated.json()) as { id: string; items: { id: string }[] };
  const pmToken = await apiToken(request, PM);
  const grnPosted = await request.post(`${API}/grns/${grn.id}/approve`, {
    headers: { Authorization: `Bearer ${pmToken}` },
    data: {},
  });
  expect(grnPosted.ok(), await grnPosted.text()).toBeTruthy();
  expect(sql(`select status from grns where id = '${grn.id}'`)[0]).toBe("POSTED");

  // --- The invoice itself, through the browser -----------------------------------------
  const page = await browser.newPage();
  await signIn(page, FINANCE);
  await page.goto("/finance/vendor-invoices/new");
  await page.getByLabel("Vendor").selectOption(vendor.id);
  const ref = `E2E-BILL-${Date.now()}`;
  await page.getByLabel("Their reference").fill(ref);
  await page.getByLabel("Invoice date").fill(new Date().toISOString().slice(0, 10));

  // The lone default direct line is dropped in favour of the GRN's own line.
  await page.getByRole("button", { name: "Add line from a GRN" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Goods received note").selectOption({ index: 1 });
  await dialog.getByRole("button", { name: "Add" }).click();
  await page.getByRole("button", { name: "Remove line 1" }).click();

  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("VINV-");
  const invoiceId = page.url().split("/").pop()!;
  expect(sql(`select status from vendor_invoices where id = '${invoiceId}'`)[0]).toBe("DRAFT");

  await page.getByRole("button", { name: "Match" }).click();
  await expect(page.getByText("Matched", { exact: true }).first()).toBeVisible();
  expect(sql(`select status from vendor_invoices where id = '${invoiceId}'`)[0]).toBe("MATCHED");
  await expect(page.getByText("Three Way")).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();

  const [status, journalEntryId] = sql(
    `select status || '|' || coalesce(journal_entry_id::text, '') from vendor_invoices where id = '${invoiceId}'`,
  )[0]!.split("|");
  expect(status).toBe("APPROVED");
  expect(journalEntryId).not.toBe("");

  // The GRN accrual (2110) was cleared and Accounts Payable (2100) credited
  // by the same 2,000 (20 t at 100), and the order's own invoiced quantity
  // now matches what was billed.
  expect(
    sql(
      `select sum(l.debit) from journal_entry_lines l
         join accounts a on a.id = l.account_id
        where l.je_id = '${journalEntryId}' and a.code = '2110'`,
    )[0],
  ).toBe("2000.0000");
  expect(
    sql(
      `select sum(l.credit) from journal_entry_lines l
         join accounts a on a.id = l.account_id
        where l.je_id = '${journalEntryId}' and a.code = '2100'`,
    )[0],
  ).toBe("2000.0000");
  expect(
    sql(`select invoiced_quantity from purchase_order_items where id = '${po.items[0]!.id}'`)[0],
  ).toBe("20.0000");
});
