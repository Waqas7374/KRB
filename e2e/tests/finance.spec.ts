import { expect, test } from "@playwright/test";

import { signIn, sql, USERS } from "./helpers";

/**
 * The general ledger's core, through the browser: someone drafts and submits a
 * manual journal entry, finance signs it, and it posts — moving the trial
 * balance by exactly what was posted. The workflow's approval step is held by
 * whoever holds the Finance Manager role, so the preparer here (admin, who
 * has no such role) is never listed as an eligible approver on their own
 * entry (docs/04 §2's separation of duties) — finance signs it instead. Admin
 * stands in for the preparer only because its password, unlike the seeded
 * accounts officer's, is never rotated by another spec in this suite.
 */
const cashInHandBalance = () =>
  Number(
    sql(
      `select coalesce(sum(l.debit) - sum(l.credit), 0) from journal_entry_lines l
         join journal_entries je on je.id = l.je_id
         join accounts a on a.id = l.account_id
        where a.code = '1110' and je.status = 'POSTED'`,
    )[0],
  );

test("draft, submit, approve and post a manual journal entry", async ({ browser }) => {
  const startCash = cashInHandBalance();

  // --- 1. It is drafted and submitted -------------------------------------------------
  const officer = await browser.newPage();
  await signIn(officer, USERS.admin);
  await officer.goto("/finance/journal-entries/new");
  await officer.getByLabel("Description").fill("Owner's cash injection into the business");
  await officer.locator('[name="lines.0.account_id"]').selectOption({ label: "1110 — Cash in Hand" });
  await officer.locator('[name="lines.0.debit"]').fill("5000");
  await officer
    .locator('[name="lines.1.account_id"]')
    .selectOption({ label: "3100 — Share Capital" });
  await officer.locator('[name="lines.1.credit"]').fill("5000");
  await officer.getByRole("button", { name: "Save draft" }).click();
  await expect(officer.getByRole("heading", { level: 1 })).toContainText("JV-");
  const jeId = officer.url().split("/").pop()!;
  const jeNumber = (await officer.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(sql(`select status from journal_entries where id = '${jeId}'`)[0]).toBe("DRAFT");
  expect(cashInHandBalance()).toBe(startCash); // a draft has not happened yet

  await officer.getByRole("button", { name: "Submit for approval" }).click();
  await officer
    .getByRole("dialog")
    .getByRole("button", { name: "Submit for approval" })
    .click();
  await expect(officer.getByText("Pending approval").first()).toBeVisible();
  // Admin holds no Finance Manager role, so it is not on this step's
  // approver list — there is nothing to click even if it tried.
  await expect(officer.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);

  // --- 2. Finance signs it; it posts at once ------------------------------------------
  const finance = await browser.newPage();
  await signIn(finance, USERS.finance);
  await finance.goto(`/finance/journal-entries/${jeId}`);
  await finance.getByRole("button", { name: "Approve", exact: true }).click();
  await finance.getByRole("dialog").getByRole("button", { name: `Approve ${jeNumber}` }).click();
  await expect(finance.getByText("Posted", { exact: true }).first()).toBeVisible();
  expect(sql(`select status from journal_entries where id = '${jeId}'`)[0]).toBe("POSTED");
  expect(cashInHandBalance()).toBe(startCash + 5000);

  // --- 3. The trial balance carries the movement --------------------------------------
  await finance.goto("/finance/trial-balance");
  const cashRow = finance.getByRole("row").filter({ hasText: "1110" });
  await expect(cashRow).toBeVisible();
});
