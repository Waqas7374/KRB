import { expect, test, type Page } from "@playwright/test";

import { signIn, sql } from "./helpers";

/**
 * Phase 2 through the browser: a site manager raises a purchase request, the
 * chain of approvers works it in their own inboxes, and PostgreSQL agrees.
 *
 * Seeded people: sm.gvh1 (site manager, GVH-S1), pm.gvh (project manager of
 * GVH), procurement (company-wide procurement manager).
 */
const REQUESTER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const PROCUREMENT = "procurement@krb.example";

/** Raise and submit a one-line request whose estimate is quantity × rate. */
async function raiseRequest(page: Page, quantity: string, rate: string): Promise<{ id: string; number: string }> {
  const justification = `E2E block A works ${Date.now()}`;
  await page.goto("/purchase-requests/new");
  await page.getByLabel("Justification").fill(justification);
  await page.getByLabel("Line 1 material").selectOption({ index: 1 });
  await page.getByLabel("Quantity").fill(quantity);
  await page.getByLabel("Estimated rate").fill(rate);
  await page.getByRole("button", { name: /Save and submit/ }).click();

  await expect(page.getByText("Pending approval").first()).toBeVisible();
  const number = (await page.getByRole("heading", { level: 1 }).innerText()).trim();
  return { id: page.url().split("/").pop()!, number };
}

test("a 50,000 request is approved by the project manager alone", async ({ browser }) => {
  const requester = await browser.newPage();
  await signIn(requester, REQUESTER);
  const pr = await raiseRequest(requester, "100", "500");

  // The trail shows the routed chain: a single step, awaiting Bilal Ahmad.
  const trail = requester.getByRole("list", { name: "Approval steps" });
  await expect(trail.getByText("1. Project manager")).toBeVisible();
  await expect(trail.getByText("Bilal Ahmad")).toBeVisible();
  // The requester cannot approve their own request: no decision buttons.
  await expect(requester.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(requester.getByRole("button", { name: "Recall" })).toBeVisible();

  // The project manager finds it in the inbox and approves it.
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Approvals" }).click();
  await pm.getByRole("link", { name: pr.number }).click();
  await expect(pm.getByRole("heading", { name: pr.number })).toBeVisible();
  await pm.getByRole("button", { name: "Approve" }).click();
  await pm.getByRole("dialog").getByRole("button", { name: `Approve ${pr.number}` }).click();
  await expect(pm.getByText("Approved", { exact: true }).first()).toBeVisible();

  // The document itself moved, atomically with the decision.
  expect(sql(`select status from purchase_requests where id = '${pr.id}'`)[0]).toBe("APPROVED");
  const actions = sql(
    `select a.action || '|' || coalesce(a.actor_name,'') from approval_actions a
       join approval_requests r on r.id = a.request_id where r.doc_id = '${pr.id}' order by a.acted_at`,
  );
  expect(actions).toEqual(["SUBMIT|Imran Shah", "APPROVE|Bilal Ahmad"]);

  // It has left the project manager's inbox.
  await pm.goto("/approvals");
  await expect(pm.getByRole("link", { name: pr.number })).toHaveCount(0);
});

test("a 500,000 request needs two steps; a rejection returns it to the author", async ({ browser }) => {
  const requester = await browser.newPage();
  await signIn(requester, REQUESTER);
  const pr = await raiseRequest(requester, "1000", "500");
  const trail = requester.getByRole("list", { name: "Approval steps" });
  await expect(trail.getByText("1. Project manager")).toBeVisible();
  await expect(trail.getByText("2. Procurement manager")).toBeVisible();

  // Step 2's approver cannot see it yet.
  const buyer = await browser.newPage();
  await signIn(buyer, PROCUREMENT);
  await buyer.goto("/approvals");
  await expect(buyer.getByRole("link", { name: pr.number })).toHaveCount(0);

  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto(`/purchase-requests/${pr.id}`);
  await pm.getByRole("button", { name: "Approve" }).click();
  await pm.getByRole("dialog").getByRole("button", { name: `Approve ${pr.number}` }).click();
  await expect(pm.getByText("Approved", { exact: true }).first()).toBeVisible();
  // Still pending overall: it moved to step 2.
  expect(sql(`select status from purchase_requests where id = '${pr.id}'`)[0]).toBe("PENDING_APPROVAL");

  // Now the procurement manager sees it, and rejecting demands a reason.
  await buyer.goto("/approvals");
  await buyer.getByRole("link", { name: pr.number }).click();
  await buyer.getByRole("button", { name: "Reject" }).click();
  const dialog = buyer.getByRole("dialog");
  const confirm = dialog.getByRole("button", { name: `Reject ${pr.number}` });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("Reason").fill("Quantity is double the BOQ figure");
  await confirm.click();
  await expect(buyer.getByText(/Rejected: Quantity is double/).first()).toBeVisible();

  // The author sees the reason and is offered Edit and Submit again.
  await requester.reload();
  await expect(requester.getByText(/Rejected: Quantity is double/).first()).toBeVisible();
  await expect(requester.getByRole("link", { name: "Edit" })).toBeVisible();
  await expect(requester.getByRole("button", { name: "Submit for approval" })).toBeVisible();
  expect(sql(`select status from purchase_requests where id = '${pr.id}'`)[0]).toBe("REJECTED");
});

test("an unpriced request is refused submission with a clear reason", async ({ page }) => {
  await signIn(page, REQUESTER);
  await page.goto("/purchase-requests/new");
  await page.getByLabel("Justification").fill("Rates to follow from the vendor");
  await page.getByLabel("Line 1 material").selectOption({ index: 1 });
  await page.getByLabel("Quantity").fill("50");
  await page.getByRole("button", { name: /Save and submit/ }).click();

  // Saved as a draft; only the routing was refused.
  await expect(page.getByText(/Saved as a draft, but not submitted/)).toBeVisible();
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/estimated rate/i).first()).toBeVisible();
});

test("workflow administrators see the chain and can simulate it; others can read only", async ({ browser }) => {
  const admin = await browser.newPage();
  await signIn(admin, "admin@krb.example");
  await admin.goto("/approval-workflows");
  await expect(admin.getByRole("heading", { name: "Approval workflows" })).toBeVisible();
  await expect(admin.getByText("Under 100,000")).toBeVisible();

  await admin.getByLabel("Total amount").fill("5000000");
  await admin.getByRole("button", { name: "Show the chain" }).click();
  await expect(admin.getByText("1,000,000 and above").first()).toBeVisible();
  for (const step of ["Project manager", "Procurement manager", "Finance manager", "Executive"]) {
    await expect(admin.getByText(step).last()).toBeVisible();
  }
  await expect(admin.getByRole("button", { name: /New version/ })).toBeVisible();

  const buyer = await browser.newPage();
  await signIn(buyer, PROCUREMENT);
  await buyer.goto("/approval-workflows");
  await expect(buyer.getByText("Under 100,000")).toBeVisible();
  await expect(buyer.getByRole("button", { name: /New version/ })).toHaveCount(0);
});
