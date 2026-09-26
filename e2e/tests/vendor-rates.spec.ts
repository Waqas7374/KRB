import { expect, test } from "@playwright/test";

import { API, apiToken, signIn, sql, uniqueNtn } from "./helpers";

/**
 * Vendor rates through the browser: a first rate takes effect at once, a large
 * change waits for finance, and when it is approved the old period is closed
 * the day before — with the whole story in an append-only history.
 *
 * The suite shares the development database and rates only move forward, so
 * each run prices a vendor it creates itself.
 */
const PROCUREMENT = "procurement@krb.example";
const FINANCE = "finance@krb.example";

const ymd = (offsetDays: number) =>
  new Date(Date.now() + offsetDays * 864e5).toISOString().slice(0, 10);

test("a rate change waits for finance, then supersedes the old period", async ({
  browser,
  request,
}) => {
  // A vendor of our own, so the run never collides with an earlier run's periods.
  const token = await apiToken(request, PROCUREMENT);
  const legalName = `E2E Rates Vendor ${Date.now()}`;
  const created = await request.post(`${API}/vendors`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { legal_name: legalName, ntn: uniqueNtn() },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const vendor = (await created.json()) as { id: string; code: string };

  const buyer = await browser.newPage();
  await signIn(buyer, PROCUREMENT);

  const propose = async (rate: string, from: string, reason: string) => {
    await buyer.goto("/vendor-rates");
    await buyer.getByRole("button", { name: "New rate" }).click();
    const dialog = buyer.getByRole("dialog");
    await dialog.getByLabel("Vendor").selectOption(vendor.id);
    await dialog.getByLabel("Material").selectOption({ index: 1 });
    await dialog.getByLabel(/^Rate/).fill(rate);
    await dialog.getByLabel("Effective from").fill(from);
    await dialog.getByLabel("Reason").fill(reason);
    await dialog.getByRole("button", { name: "Submit rate" }).click();
  };

  // 1. The first rate has nothing to compare with, so it takes effect at once.
  await propose("100", ymd(0), "E2E opening price");
  await expect(buyer.getByText("Rate recorded and in force.")).toBeVisible();
  expect(
    sql(`select status from vendor_rates where vendor_id = '${vendor.id}' order by effective_from`),
  ).toEqual(["ACTIVE"]);

  // 2. A 40 % increase is more than the default tolerance: it needs finance.
  await propose("140", ymd(1), "E2E cement price spike");
  await expect(buyer.getByText(/takes effect once approved/)).toBeVisible();
  expect(
    sql(`select status from vendor_rates where vendor_id = '${vendor.id}' order by effective_from`),
  ).toEqual(["ACTIVE", "PENDING_APPROVAL"]);
  // Until it is approved, the old rate is the one in force.
  expect(
    sql(
      `select rate::numeric(18,2) from vendor_rates where vendor_id = '${vendor.id}'
         and status = 'ACTIVE' and effective_to is null`,
    ),
  ).toEqual(["100.00"]);

  // 3. Finance finds it in the inbox, reads the move, and approves.
  const finance = await browser.newPage();
  await signIn(finance, FINANCE);
  await finance.goto("/approvals");
  await finance.getByRole("link", { name: new RegExp(`^${vendor.code} /`) }).click();
  await expect(finance.getByText(/\+40\.00%/).first()).toBeVisible();
  await finance.getByRole("button", { name: "Approve", exact: true }).click();
  await finance
    .getByRole("dialog")
    .getByRole("button", { name: /^Approve / })
    .click();
  await expect(finance.getByText("Approved", { exact: true }).first()).toBeVisible();

  // 4. The old period was closed the day before the new one starts; both are approved.
  expect(
    sql(
      `select effective_from || '>' || coalesce(effective_to::text, 'open') || '>' || status
         from vendor_rates where vendor_id = '${vendor.id}' order by effective_from`,
    ),
  ).toEqual([`${ymd(0)}>${ymd(0)}>ACTIVE`, `${ymd(1)}>open>ACTIVE`]);

  // 5. The history keeps who, why, and old -> new. It cannot be edited.
  expect(
    sql(
      `select coalesce(old_rate::numeric(18,2)::text, '-') || '>' || new_rate::numeric(18,2) || '>' || reason
         from vendor_rate_history where vendor_id = '${vendor.id}' order by changed_at`,
    ),
  ).toEqual(["->100.00>E2E opening price", "100.00>140.00>E2E cement price spike"]);

  await buyer.goto("/vendor-rates");
  await buyer.getByRole("button", { name: new RegExp(`History of ${legalName}`) }).first().click();
  const drawer = buyer.getByRole("dialog");
  await expect(drawer.getByText("“E2E cement price spike”")).toBeVisible();
  await expect(drawer.getByText("“E2E opening price”")).toBeVisible();
});
