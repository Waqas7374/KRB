import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn, USERS } from "./helpers";

/**
 * docs/10-roadmap.md, Phase 1 "done when":
 *   creating a vendor in the browser creates a row in PostgreSQL with an
 *   audit entry, an Auditor role can read it and cannot write it.
 */
test("a vendor created in the browser is stored, audited, and read-only to an auditor", async ({
  browser,
  request,
}) => {
  const legalName = `E2E Stone Crushers ${Date.now()}`;
  const ntn = uniqueNtn();

  // --- Procurement manager creates the vendor through the UI ---------------
  const buyer = await browser.newPage();
  await signIn(buyer, USERS.procurement);
  await buyer.getByRole("link", { name: "Vendors" }).first().click();
  await buyer.getByRole("link", { name: "New vendor" }).click();

  await buyer.getByLabel("Legal name").fill(legalName);
  await buyer.getByLabel("NTN").fill(ntn);
  await buyer.getByLabel("Payment terms (days)").fill("45");
  await buyer
    .getByRole("group", { name: "Contact", exact: true })
    .getByRole("textbox", { name: "Phone" })
    .fill("042-35000000");
  await buyer.getByRole("button", { name: "Add contact" }).click();
  const contact = buyer.getByRole("group", { name: "Contact people" });
  await contact.getByRole("textbox", { name: "Name", exact: true }).fill("Asif Iqbal");
  await contact.getByRole("textbox", { name: "Phone" }).fill("0300-5550000");
  await buyer.getByRole("button", { name: "Create vendor" }).click();

  await expect(buyer.getByRole("heading", { name: legalName })).toBeVisible();
  await expect(buyer.getByText("Draft", { exact: true })).toBeVisible();
  const vendorUrl = buyer.url();
  const vendorId = vendorUrl.split("/").pop()!;

  // --- PostgreSQL has the row, and an audit entry for its creation ---------
  const [row] = sql(`select status || '|' || ntn || '|' || payment_terms_days from vendors where id = '${vendorId}'`);
  expect(row).toBe(`DRAFT|${ntn}|45`);
  const audit = sql(
    `select action || '|' || coalesce(actor_name, '') from audit_logs where entity_id = '${vendorId}' and action = 'CREATE'`,
  );
  expect(audit.length).toBeGreaterThanOrEqual(1);
  expect(audit[0]).toContain("Ahmed Raza"); // the procurement manager, not "system"

  // --- The auditor can open it but is offered no way to change it ----------
  const auditor = await browser.newPage();
  await signIn(auditor, USERS.auditor);
  await expect(auditor.getByText("Read-only access")).toBeVisible();
  await auditor.goto(vendorUrl);
  await expect(auditor.getByRole("heading", { name: legalName })).toBeVisible();
  await expect(auditor.getByText(ntn)).toBeVisible();
  for (const action of ["Edit", "Approve", "Suspend", "Blacklist"]) {
    await expect(auditor.getByRole("button", { name: action })).toHaveCount(0);
    await expect(auditor.getByRole("link", { name: action })).toHaveCount(0);
  }
  await auditor.goto(`${vendorUrl}/edit`);
  await expect(auditor.getByText("You do not have access to this")).toBeVisible();

  // --- ...and the API refuses the write regardless of the UI ---------------
  const token = await apiToken(request, USERS.auditor);
  const attempt = await request.patch(`${API}/vendors/${vendorId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { legal_name: "Changed by the auditor" },
  });
  expect(attempt.status()).toBe(403);
  expect(sql(`select legal_name from vendors where id = '${vendorId}'`)[0]).toBe(legalName);
});

test("the vendor approval flow works end to end in the browser", async ({ page }) => {
  const legalName = `E2E Approvable Supplier ${Date.now()}`;
  await signIn(page, USERS.procurement);
  await page.goto("/vendors/new");
  await page.getByLabel("Legal name").fill(legalName);
  await page.getByLabel("NTN").fill(uniqueNtn());
  await page.getByRole("button", { name: "Create vendor" }).click();
  await expect(page.getByRole("heading", { name: legalName })).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: /^Approve / }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("Can be used on purchase orders")).toBeVisible();

  const vendorId = page.url().split("/").pop()!;
  expect(sql(`select status from vendors where id = '${vendorId}'`)[0]).toBe("ACTIVE");

  // Suspending demands a reason; the confirm button stays disabled without one.
  await page.getByRole("button", { name: "Suspend" }).click();
  const suspend = page.getByRole("dialog");
  const confirm = suspend.getByRole("button", { name: /^Suspend / });
  await expect(confirm).toBeDisabled();
  await suspend.getByLabel("Reason").fill("Late deliveries on three consecutive orders");
  await confirm.click();
  await expect(page.getByText(/Suspended: Late deliveries/)).toBeVisible();
  expect(sql(`select status from vendors where id = '${vendorId}'`)[0]).toBe("SUSPENDED");
});
