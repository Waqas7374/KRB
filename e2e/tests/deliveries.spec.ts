import { expect, test } from "@playwright/test";

import { signIn, sql } from "./helpers";

/**
 * The core of the product through the browser: site staff record an overloaded,
 * off-site delivery with no order; it is saved and flagged, never refused; a
 * reviewer sends it back, the capturer corrects it, and it is checked afresh;
 * a critical flag cannot be accepted without a reason; the decision is kept.
 *
 * GVH-S1's centre is (31.411, 74.2461) with a 500 m radius; 0.009 degrees of
 * latitude is about 1 000 m, so the truck below is ~500 m outside the fence.
 */
const STAFF = "staff.gvh1@krb.example";
const MANAGER = "sm.gvh1@krb.example";

test("record, flag, send back, correct, and approve a delivery", async ({ browser }) => {
  // --- Site staff record it: 22 t on a 16 t truck, 1 km north of the site -------------
  const staffContext = await browser.newContext({
    geolocation: { latitude: 31.42, longitude: 74.2461, accuracy: 8 },
    permissions: ["geolocation"],
  });
  const staff = await staffContext.newPage();
  await signIn(staff, STAFF);
  await staff.goto("/deliveries/new");
  await staff.getByLabel("Site").selectOption({ index: 1 });
  await staff.getByLabel("Vendor").selectOption({ index: 1 });
  const crush = await staff
    .locator("select[name='items.0.material_id'] option", { hasText: "AGG-CRUSH-12" })
    .getAttribute("value");
  await staff.getByLabel("Line 1 material").selectOption(crush!);
  await staff.getByLabel("Quantity").fill("22");
  const truckType = await staff
    .locator("select[name='truck_type_id'] option", { hasText: "10-WHEELER" })
    .getAttribute("value");
  await staff.getByLabel("Truck type").selectOption(truckType!);
  await staff.getByLabel("Truck number").fill(`E2E-${Date.now() % 100000}`);
  await staff.getByRole("button", { name: "Use my current location" }).click();
  await expect(staff.getByText(/±8(.0)? m/)).toBeVisible();
  await staff.getByRole("button", { name: "Record delivery" }).click();

  // Saved, never refused: the detail page lists what was flagged.
  await expect(staff.getByText(/flags? raised — sent for review/)).toBeVisible();
  const number = (await staff.getByRole("heading", { level: 1 }).innerText()).trim();
  const id = staff.url().split("/").pop()!;
  expect(number).toMatch(/^DLV-/);
  await expect(staff.getByText("Tonnage anomaly")).toBeVisible();
  await expect(staff.getByText("Geofence mismatch")).toBeVisible();
  await expect(staff.getByText("No po", { exact: false }).first()).toBeVisible();
  await expect(staff.getByText(/Tonnage 22\.0t exceeds 16\.0t maximum/)).toBeVisible();
  // Site staff decide nothing.
  await expect(staff.getByRole("button", { name: "Approve" })).toHaveCount(0);
  expect(sql(`select status from deliveries where id = '${id}'`)[0]).toBe("UNDER_REVIEW");
  expect(
    sql(
      `select flag_type from delivery_flags where delivery_id = '${id}' and severity <> 'INFO' order by flag_type`,
    ),
  ).toEqual(expect.arrayContaining(["GEOFENCE_MISMATCH", "NO_PO", "TONNAGE_ANOMALY"]));

  // --- The reviewer finds it in the queue and sends it back ---------------------------
  const manager = await browser.newPage();
  await signIn(manager, MANAGER);
  await manager.goto("/deliveries/review");
  await manager.getByRole("link", { name: number }).click();
  await manager.getByRole("button", { name: "Send back" }).click();
  const back = manager.getByRole("dialog");
  const send = back.getByRole("button", { name: "Send back" });
  await expect(send).toBeDisabled();
  await back.getByLabel("What needs correcting").fill("22 t on a 16 t truck: check the weighbridge slip");
  await send.click();
  await expect(manager.getByText("Correction requested").first()).toBeVisible();
  expect(sql(`select status from deliveries where id = '${id}'`)[0]).toBe("CORRECTION_REQUESTED");

  // --- The capturer sees the note, corrects the load, and it is checked afresh --------
  await staff.reload();
  await expect(staff.getByText(/Sent back by Imran Shah/)).toBeVisible();
  await staff.getByRole("link", { name: "Correct" }).click();
  await expect(staff.getByText(/asked: 22 t on a 16 t truck/)).toBeVisible();
  await staff.getByLabel("Quantity").fill("15");
  await staff.getByRole("button", { name: "Submit correction" }).click();
  await expect(staff.getByText("Under review").first()).toBeVisible();
  expect(
    sql(
      `select status from delivery_flags where delivery_id = '${id}' and flag_type = 'TONNAGE_ANOMALY'`,
    ),
  ).toEqual(["CORRECTED"]);

  // --- A critical flag needs a reason to accept; the decision is kept -----------------
  await manager.goto(`/deliveries/${id}`);
  await manager.getByRole("button", { name: "Approve" }).click();
  const approve = manager.getByRole("dialog");
  const confirm = approve.getByRole("button", { name: `Approve ${number}` });
  const critical = sql(
    `select count(*) from delivery_flags where delivery_id = '${id}' and severity = 'CRITICAL' and status = 'OPEN'`,
  )[0];
  if (critical !== "0") {
    await expect(confirm).toBeDisabled();
    await approve.getByLabel("Why is this acceptable?").fill("Truck stopped at the gate first; load verified by hand");
  }
  await confirm.click();
  await expect(manager.getByText("Approved", { exact: true }).first()).toBeVisible();

  expect(sql(`select status from deliveries where id = '${id}'`)[0]).toBe("APPROVED");
  expect(
    sql(`select action from delivery_reviews where delivery_id = '${id}' order by reviewed_at`),
  ).toEqual(["REQUEST_CORRECTION", "CORRECTION_SUBMITTED", "ACCEPT"]);
  await staffContext.close();
});
