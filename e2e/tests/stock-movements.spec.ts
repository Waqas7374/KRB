import { expect, test } from "@playwright/test";

import { signIn, sql } from "./helpers";

/**
 * Stock moving other than by a receipt, through the browser: an adjustment puts
 * stock on the shelf (it needs a signature), an issue takes some out, a transfer
 * carries some to another site and is counted in there. PostgreSQL is asserted at
 * each step, including that a draft moves nothing.
 *
 * The suite shares the development database, so every assertion is a *change*
 * from a baseline read first, and the stock is put in at a cost of 100 (used
 * only if the store is empty; otherwise the current average applies).
 */
const MANAGER = "sm.gvh1@krb.example";
const PM = "pm.gvh@krb.example";
const STORE = "store.cs@krb.example";

const crush = () => sql(`select id from materials where sku = 'AGG-CRUSH-12'`)[0]!;
const warehouse = (code: string) => sql(`select id from warehouses where code = '${code}'`)[0]!;
const onHand = (code: string) =>
  Number(
    sql(
      `select coalesce(sum(b.quantity_on_hand), 0) from inventory_balances b
         join warehouses w on w.id = b.warehouse_id
        where w.code = '${code}' and b.material_id = '${crush()}'`,
    )[0],
  );

test("adjust in, issue out, transfer across sites", async ({ browser }) => {
  const godown = warehouse("GVH-S1-GODOWN");
  const central = warehouse("CS-LHR-MAIN");
  const startGodown = onHand("GVH-S1-GODOWN");
  const startCentral = onHand("CS-LHR-MAIN");

  // --- 1. The site manager raises an adjustment: it moves nothing until it is signed -------
  const manager = await browser.newPage();
  await signIn(manager, MANAGER);
  await manager.goto("/inventory/adjustments/new");
  await manager.getByLabel(/^Store/).selectOption(godown);
  await manager.getByLabel(/^Reason/).selectOption("OPENING_BALANCE");
  await manager.getByLabel("What happened").fill("Stock brought in from the old ledger");
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Change").selectOption("increase");
  await manager.getByLabel(/^Quantity/).fill("20");
  await manager.getByLabel("Cost per unit").fill("100");
  await manager.getByRole("button", { name: "Save draft" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("ADJ-");
  const adjustmentId = manager.url().split("/").pop()!;
  const adjustmentNumber = (await manager.getByRole("heading", { level: 1 }).innerText()).trim();
  expect(sql(`select status from stock_adjustments where id = '${adjustmentId}'`)[0]).toBe("DRAFT");
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown);

  await manager.getByRole("button", { name: "Submit for approval" }).click();
  await manager
    .getByRole("dialog")
    .getByRole("button", { name: "Submit for approval" })
    .click();
  await expect(manager.getByText("Pending approval").first()).toBeVisible();
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown); // awaiting a signature is not movement
  // The person who raised it cannot sign it.
  await expect(manager.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);

  // --- 2. The project manager signs it; the stock is on the shelf -------------------------
  const pm = await browser.newPage();
  await signIn(pm, PM);
  await pm.goto(`/inventory/adjustments/${adjustmentId}`);
  await pm.getByRole("button", { name: "Approve", exact: true }).click();
  await pm.getByRole("dialog").getByRole("button", { name: `Approve ${adjustmentNumber}` }).click();
  await expect(pm.getByText("Posted", { exact: true }).first()).toBeVisible();
  expect(sql(`select status from stock_adjustments where id = '${adjustmentId}'`)[0]).toBe("POSTED");
  expect(
    sql(
      `select txn_type || '|' || quantity_in::numeric(18,2) from inventory_transactions
        where source_id = '${adjustmentId}'`,
    ),
  ).toEqual(["ADJUST_IN|20.00"]);
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown + 20);

  // --- 3. An issue takes 5 out, at the store's average cost -------------------------------
  await manager.goto("/inventory/issues/new");
  await manager.getByLabel(/^Store/).selectOption(godown);
  await manager.getByLabel(/^Name/).fill("Al-Noor Contractors");
  await manager.getByLabel("Purpose").fill("Road base, block C");
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Quantity").fill("5");
  await manager.getByRole("button", { name: "Issue material" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("ISS-");
  await expect(manager.getByText("Issued", { exact: true }).first()).toBeVisible();
  const issueId = manager.url().split("/").pop()!;
  expect(
    sql(
      `select txn_type || '|' || quantity_out::numeric(18,2) from inventory_transactions
        where source_id = '${issueId}'`,
    ),
  ).toEqual(["ISSUE_OUT|5.00"]);
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown + 15);
  // The site manager may not see what the stock cost.
  await expect(manager.getByText("Hidden").first()).toBeVisible();

  // --- 4. It cannot be issued twice over: more than is there is refused -------------------
  await manager.goto("/inventory/issues/new");
  await manager.getByLabel(/^Store/).selectOption(godown);
  await manager.getByLabel(/^Name/).fill("Al-Noor Contractors");
  await manager.getByLabel("Purpose").fill("More than there is");
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Quantity").fill("1000000");
  await manager.getByRole("button", { name: "Issue material" }).click();
  await expect(manager.getByText(/saved as a draft but not posted/).first()).toBeVisible();
  await expect(manager.getByText("Draft", { exact: true }).first()).toBeVisible();
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown + 15);

  // --- 5. A transfer carries 8 to the central store; it is in transit until counted in ---
  await manager.goto("/inventory/transfers/new");
  await manager.getByLabel(/^From/).selectOption(godown);
  await manager.getByLabel(/^To(\*|$)/).selectOption(central);
  await manager.getByLabel("Vehicle").fill("LEA-4411");
  await manager.getByLabel("Line 1 material").selectOption(crush());
  await manager.getByLabel("Quantity").fill("8");
  await manager.getByRole("button", { name: "Dispatch stock" }).click();
  await expect(manager.getByRole("heading", { level: 1 })).toContainText("TRF-");
  await expect(manager.getByText("In transit").first()).toBeVisible();
  const transferId = manager.url().split("/").pop()!;
  expect(onHand("GVH-S1-GODOWN")).toBe(startGodown + 7);
  expect(onHand("CS-LHR-MAIN")).toBe(startCentral); // on the truck, not on the shelf
  expect(
    sql(
      `select b.quantity_in_transit from inventory_balances b
        where b.warehouse_id = '${central}' and b.material_id = '${crush()}'`,
    )[0],
  ).toBe("8.0000");
  // The sender cannot count it in at the other end.
  await expect(manager.getByRole("button", { name: "Receive", exact: true })).toHaveCount(0);

  // --- 6. The central store receives it ---------------------------------------------------
  const store = await browser.newPage();
  await signIn(store, STORE);
  await store.goto(`/inventory/transfers/${transferId}`);
  await store.getByRole("button", { name: "Receive", exact: true }).click();
  await store.getByRole("dialog").getByRole("button", { name: "Receive", exact: true }).click();
  await expect(store.getByText("Received", { exact: true }).first()).toBeVisible();
  expect(onHand("CS-LHR-MAIN")).toBe(startCentral + 8);
  expect(
    sql(
      `select b.quantity_in_transit from inventory_balances b
        where b.warehouse_id = '${central}' and b.material_id = '${crush()}'`,
    )[0],
  ).toBe("0.0000");
  expect(
    sql(
      `select txn_type from inventory_transactions where source_id = '${transferId}' order by posted_at`,
    ),
  ).toEqual(["TRANSFER_OUT", "TRANSFER_IN"]);
  // Moving stock never changes its value: the cost it left at is the cost it arrived at.
  expect(
    sql(
      `select count(distinct unit_cost) from inventory_transactions where source_id = '${transferId}'`,
    )[0],
  ).toBe("1");
});
