import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn } from "./helpers";

/**
 * A payment request through the browser: raised, submitted, signed by
 * finance (below the 200,000 tier), executed as a payment against a real
 * bank account, and allocated to settle a real vendor invoice — asserted
 * against PostgreSQL at each step. The invoice itself is built directly
 * through the API (it is already exercised, end to end through the browser,
 * by vendor-invoices.spec.ts); this spec is the one that is new here: the
 * payment-request and payment screens themselves.
 */
const ADMIN = "admin@krb.example";
const PROCUREMENT = "procurement@krb.example";
const FINANCE = "finance@krb.example";

test("a payment request is raised, approved, executed and allocated", async ({
  browser,
  request,
}) => {
  const adminToken = await apiToken(request, ADMIN);
  const adminHeaders = { Authorization: `Bearer ${adminToken}` };
  const legalName = `E2E Payment Vendor ${Date.now()}`;
  const vendorCreated = await request.post(`${API}/vendors`, {
    headers: adminHeaders,
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

  // --- A direct-line invoice, matched and approved, waiting to be paid ----------------
  const financeToken = await apiToken(request, FINANCE);
  const financeHeaders = { Authorization: `Bearer ${financeToken}` };
  const accountsRes = await request.get(`${API}/finance/accounts`, {
    headers: financeHeaders,
    params: { limit: 200 },
  });
  const accounts = ((await accountsRes.json()) as { items: { id: string; code: string }[] }).items;
  const account5100 = accounts.find((a) => a.code === "5100")!.id;

  const invoiceCreated = await request.post(`${API}/finance/vendor-invoices`, {
    headers: financeHeaders,
    data: {
      vendor_id: vendor.id,
      vendor_invoice_ref: `E2E-BILL-${Date.now()}`,
      invoice_date: new Date().toISOString().slice(0, 10),
      items: [{ quantity: "1", rate: "6000", account_id: account5100 }],
    },
  });
  expect(invoiceCreated.ok(), await invoiceCreated.text()).toBeTruthy();
  const invoice = (await invoiceCreated.json()) as { id: string; version: number };
  const matched = await request.post(`${API}/finance/vendor-invoices/${invoice.id}/match`, {
    headers: { ...financeHeaders, "If-Match": String(invoice.version) },
  });
  expect(matched.ok(), await matched.text()).toBeTruthy();
  const matchedInvoice = (await matched.json()) as { version: number };
  const invoiceApproved = await request.post(
    `${API}/finance/vendor-invoices/${invoice.id}/approve`,
    { headers: { ...financeHeaders, "If-Match": String(matchedInvoice.version) } },
  );
  expect(invoiceApproved.ok(), await invoiceApproved.text()).toBeTruthy();

  // --- Raise and submit the payment request, through the browser ----------------------
  const admin = await browser.newPage();
  await signIn(admin, ADMIN);
  await admin.goto("/finance/payment-requests/new");
  await admin.getByLabel("Vendor").selectOption(vendor.id);
  await admin.getByLabel("Amount").fill("6000");
  await admin.getByLabel("Reason").fill("Settling the E2E test invoice");
  await admin.getByRole("button", { name: "Save draft" }).click();
  await expect(admin.getByRole("heading", { level: 1 })).toContainText("PREQ-");
  const requestNumber = (await admin.getByRole("heading", { level: 1 }).innerText()).trim();
  const requestId = admin.url().split("/").pop()!;

  await admin.getByRole("button", { name: "Submit for approval" }).click();
  await admin.getByRole("dialog").getByRole("button", { name: "Submit for approval" }).click();
  await expect(admin.getByText("Pending approval", { exact: true }).first()).toBeVisible();

  // --- Finance signs it (below the 200,000 tier: finance alone) -----------------------
  const finance = await browser.newPage();
  await signIn(finance, FINANCE);
  await finance.goto(`/finance/payment-requests/${requestId}`);
  await finance.getByRole("button", { name: "Approve", exact: true }).click();
  await finance
    .getByRole("dialog")
    .getByRole("button", { name: `Approve ${requestNumber}` })
    .click();
  await expect(finance.getByText("Approved", { exact: true }).first()).toBeVisible();
  expect(sql(`select status from payment_requests where id = '${requestId}'`)[0]).toBe("APPROVED");

  // --- Execute the payment ------------------------------------------------------------
  await finance.goto("/finance/payments/new");
  await finance.getByLabel("Approved payment request").selectOption(requestId);
  await finance
    .getByLabel("From account")
    .selectOption({ label: "Main Operating Account — Sample Bank Ltd" });
  await finance.getByRole("button", { name: "Issue payment" }).click();
  await expect(finance.getByRole("heading", { level: 1 })).toContainText("PMT-");
  const paymentId = finance.url().split("/").pop()!;
  expect(sql(`select status from payments where id = '${paymentId}'`)[0]).toBe("ISSUED");
  expect(sql(`select status from payment_requests where id = '${requestId}'`)[0]).toBe("PAID");

  const journalEntryId = sql(`select journal_entry_id from payments where id = '${paymentId}'`)[0]!;
  expect(journalEntryId).not.toBe("");

  // --- Allocate it against the invoice --------------------------------------------------
  await finance.getByRole("button", { name: "Allocate" }).click();
  const dialog = finance.getByRole("dialog");
  await dialog.getByRole("button", { name: "Add invoice" }).click();
  await dialog.getByRole("combobox").first().selectOption({ index: 1 });
  await dialog.getByPlaceholder("Amount").fill("6000");
  await dialog.getByRole("button", { name: "Save allocation" }).click();
  await expect(finance.getByText("Nothing allocated yet.")).toHaveCount(0);

  expect(sql(`select status from vendor_invoices where id = '${invoice.id}'`)[0]).toBe("PAID");
  expect(
    sql(`select paid_amount::numeric(18,2) from vendor_invoices where id = '${invoice.id}'`)[0],
  ).toBe("6000.00");
  expect(
    sql(`select allocated_amount::numeric(18,2) from payments where id = '${paymentId}'`)[0],
  ).toBe("6000.00");
});
