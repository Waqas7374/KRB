import { expect, test, type Page } from "@playwright/test";

import { signIn, sql } from "./helpers";

/**
 * Phase 2 part 2 through the browser: an approved purchase request becomes an
 * RFQ, three vendors quote, a person chooses one and says why, the order is
 * approved, and the request's lines are marked as sourced — with PostgreSQL
 * checked alongside every step that matters.
 *
 * Seeded people: sm.gvh1 (site manager, GVH-S1), pm.gvh (project manager),
 * procurement (procurement manager), finance (finance manager).
 */
const REQUESTER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const PROCUREMENT = "procurement@krb.example";
const FINANCE = "finance@krb.example";

async function decide(page: Page, number: string, verb = "Approve"): Promise<void> {
  await page.goto("/approvals");
  await page.getByRole("link", { name: number }).click();
  await page.getByRole("button", { name: verb, exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: `${verb} ${number}` }).click();
}

async function approvedRequest(browser: import("@playwright/test").Browser): Promise<{
  id: string;
  number: string;
}> {
  const requester = await browser.newPage();
  await signIn(requester, REQUESTER);
  await requester.goto("/purchase-requests/new");
  await requester.getByLabel("Justification").fill(`E2E sourcing ${Date.now()}`);
  await requester.getByLabel("Line 1 material").selectOption({ index: 1 });
  await requester.getByLabel("Quantity").fill("100");
  await requester.getByLabel("Estimated rate").fill("500");
  await requester.getByRole("button", { name: /Save and submit/ }).click();
  await expect(requester.getByText("Pending approval").first()).toBeVisible();
  const number = (await requester.getByRole("heading", { level: 1 }).innerText()).trim();
  const id = requester.url().split("/").pop()!;

  const pm = await browser.newPage();
  await signIn(pm, PM);
  await decide(pm, number);
  await expect(pm.getByText("Approved", { exact: true }).first()).toBeVisible();
  expect(sql(`select status from purchase_requests where id = '${id}'`)[0]).toBe("APPROVED");
  return { id, number };
}

test("request -> RFQ -> three quotations -> selection with a reason -> approved order -> sourced", async ({
  browser,
}) => {
  const pr = await approvedRequest(browser);
  const buyer = await browser.newPage();
  await signIn(buyer, PROCUREMENT);

  // --- RFQ from the approved request, three vendors, issued -----------------------
  await buyer.goto(`/purchase-requests/${pr.id}`);
  await buyer.getByRole("link", { name: "Request quotations" }).click();
  await expect(buyer.getByRole("heading", { name: "New RFQ" })).toBeVisible();
  await buyer.getByLabel("Responses due by").fill(
    new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10),
  );
  const vendorBoxes = buyer.getByRole("checkbox", { name: /^VEN-/ });
  for (const i of [0, 1, 2]) await vendorBoxes.nth(i).check();
  await buyer.getByRole("button", { name: "Save and issue" }).click();
  await expect(buyer.getByText("Issued", { exact: true }).first()).toBeVisible();
  const rfqNumber = (await buyer.getByRole("heading", { level: 1 }).innerText()).trim();
  const rfqId = buyer.url().split("/").pop()!;
  expect(rfqNumber).toMatch(/^RFQ-/);
  expect(sql(`select status from rfqs where id = '${rfqId}'`)[0]).toBe("ISSUED");

  // --- Three vendors quote: 480, 450 and a partial 400 ---------------------------
  const record = async (index: number, rate: string): Promise<void> => {
    await buyer.goto(`/rfqs/${rfqId}`);
    await buyer.getByRole("link", { name: "Record quotation" }).first().click();
    await buyer.getByLabel("Line 1 rate").fill(rate);
    await buyer.getByRole("button", { name: "Record quotation" }).click();
    await expect(buyer.getByRole("heading", { level: 1 })).toContainText("QT-");
    void index;
  };
  await record(0, "480");
  await record(1, "450");
  await record(2, "470");
  expect(sql(`select count(*) from vendor_quotations where rfq_id = '${rfqId}'`)[0]).toBe("3");

  // --- The comparison marks the lowest; selecting needs a real reason ------------
  await buyer.goto(`/rfqs/${rfqId}/comparison`);
  await expect(buyer.getByText("Lowest").first()).toBeVisible();
  const lowestRow = buyer.locator("td", { hasText: "Lowest" }).first();
  await expect(lowestRow).toContainText("450");

  const pickButtons = buyer.getByRole("button", { name: /^Select / });
  await expect(pickButtons).toHaveCount(3);
  // Vendors appear in invitation order; the second quoted 450.
  const winnerLabel = (await pickButtons.nth(1).getAttribute("aria-label"))!;
  await pickButtons.nth(1).click();
  const dialog = buyer.getByRole("dialog");
  const confirm = dialog.getByRole("button", { name: winnerLabel });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel(/Reason for choosing/).fill("cheapest");
  await expect(confirm).toBeDisabled(); // a single word is not a reason
  const reason = "Lowest net rate for the full quantity, and can deliver within the week.";
  await dialog.getByLabel(/Reason for choosing/).fill(reason);
  await confirm.click();
  await expect(buyer.getByText(/^Selected:/)).toBeVisible();

  const selected = sql(
    `select status || '|' || selection_reason from vendor_quotations where rfq_id = '${rfqId}' and status = 'SELECTED'`,
  );
  expect(selected).toEqual([`SELECTED|${reason}`]);

  // --- Order from the selected quotation, submitted for approval -----------------
  await buyer.getByRole("link", { name: /Open the quotation/ }).click();
  await expect(buyer.getByText(reason)).toBeVisible();
  await buyer.getByRole("button", { name: "Create purchase order" }).click();
  await expect(buyer.getByRole("heading", { level: 1 })).toContainText("PO-");
  const poNumber = (await buyer.getByRole("heading", { level: 1 }).innerText()).replace(/\s*rev.*/, "").trim();
  const poId = buyer.url().split("/").pop()!;
  await expect(buyer.getByText("Draft", { exact: true }).first()).toBeVisible();
  // 100 x 450 = 45 000, no tax quoted.
  expect(sql(`select total_amount::numeric(18,2) from purchase_orders where id = '${poId}'`)[0]).toBe("45000.00");
  // Nothing is claimed on the request until the order is approved.
  expect(sql(`select sourced_quantity::int from purchase_request_items where request_id = '${pr.id}'`)[0]).toBe("0");

  await buyer.getByRole("button", { name: "Submit for approval" }).click();
  await expect(buyer.getByText("Pending approval").first()).toBeVisible();
  // Procurement raised it, so procurement cannot approve it: no decision buttons.
  await expect(buyer.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);

  // --- Finance signs; the request is sourced and the RFQ closed ------------------
  const finance = await browser.newPage();
  await signIn(finance, FINANCE);
  await decide(finance, poNumber);
  await expect(finance.getByText("Approved", { exact: true }).first()).toBeVisible();

  expect(sql(`select status from purchase_orders where id = '${poId}'`)[0]).toBe("APPROVED");
  expect(sql(`select sourced_quantity::int from purchase_request_items where request_id = '${pr.id}'`)[0]).toBe("100");
  expect(sql(`select status from purchase_requests where id = '${pr.id}'`)[0]).toBe("SOURCED");
  expect(sql(`select status from rfqs where id = '${rfqId}'`)[0]).toBe("CLOSED");

  // --- The buyer sends it, and the request page shows it fully sourced -----------
  await buyer.goto(`/purchase-orders/${poId}`);
  await buyer.getByRole("button", { name: "Mark as sent" }).click();
  await expect(buyer.getByText("Sent", { exact: true }).first()).toBeVisible();

  // The printable order downloads under its own number and is a real PDF.
  const [download] = await Promise.all([
    buyer.waitForEvent("download"),
    buyer.getByRole("button", { name: "Download PDF" }).click(),
  ]);
  expect(download.suggestedFilename()).toBe(`${poNumber}.pdf`);
  const header = (await import("node:fs")).readFileSync((await download.path())!).subarray(0, 5);
  expect(header.toString()).toBe("%PDF-");

  // --- A site manager may see the order but not its prices -----------------------
  const site = await browser.newPage();
  await signIn(site, REQUESTER);
  await site.goto(`/purchase-orders/${poId}`);
  await expect(site.getByText("Prices hidden for your role")).toBeVisible();
  await expect(site.getByText("45,000")).toHaveCount(0);
  await expect(site.getByText("Hidden").first()).toBeVisible();

  // --- Amending reopens the order and gives the request its units back -----------
  await buyer.goto(`/purchase-orders/${poId}`);
  await buyer.getByRole("button", { name: "Amend" }).click();
  const amend = buyer.getByRole("dialog");
  await amend.getByLabel(/What is changing/).fill("Vendor revised the quantity");
  await amend.getByRole("button", { name: "Reopen for amendment" }).click();
  await expect(buyer.getByText(/Reopened for amendment 1/)).toBeVisible();
  await expect(buyer.getByText("Draft", { exact: true }).first()).toBeVisible();
  expect(sql(`select sourced_quantity::int from purchase_request_items where request_id = '${pr.id}'`)[0]).toBe("0");
  expect(sql(`select status from purchase_requests where id = '${pr.id}'`)[0]).toBe("APPROVED");

  // --- Cancelling the reopened order reopens the RFQ so another vendor can be chosen
  await buyer.getByRole("button", { name: /^Cancel order/ }).click();
  const cancel = buyer.getByRole("dialog");
  await cancel.getByLabel("Reason").fill("Vendor withdrew the offer");
  await cancel.getByRole("button", { name: `Cancel ${poNumber}` }).click();
  await expect(buyer.getByText(/Cancelled: Vendor withdrew/)).toBeVisible();
  expect(sql(`select status from purchase_orders where id = '${poId}'`)[0]).toBe("CANCELLED");
  expect(sql(`select status from rfqs where id = '${rfqId}'`)[0]).toBe("ISSUED");
});
