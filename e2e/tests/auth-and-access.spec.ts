import { expect, test } from "@playwright/test";

import { DEV_PASSWORD, signIn, USERS } from "./helpers";

const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";

test("a wrong password is refused with a clear message and no session", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email or phone").fill(USERS.admin);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("do not match");
  await expect(page).toHaveURL(/\/login$/);
});

test("a session survives a reload and ends on sign-out", async ({ page }) => {
  await signIn(page, USERS.admin);
  await page.goto("/projects");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();

  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  // The protected page now bounces to sign-in.
  await page.goto("/projects");
  await expect(page).toHaveURL(/\/login/);
});

test("site staff do not see admin screens and are refused if they navigate there", async ({ page }) => {
  await signIn(page, USERS.siteStaff);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link", { name: "Vendors" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Users" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Roles" })).toHaveCount(0);

  await page.goto("/users");
  await expect(page.getByText("You do not have access to this")).toBeVisible();
  // Site staff can view vendors but cannot create one.
  await page.goto("/vendors");
  await expect(page.getByRole("link", { name: "New vendor" })).toHaveCount(0);
});

test("forgot password sends a real email whose link sets a new password", async ({ page, request }) => {
  const email = USERS.accounts;
  await request.delete(`${MAILPIT}/api/v1/messages`);

  await page.goto("/login");
  await page.getByRole("link", { name: "Forgot your password?" }).click();
  await page.getByLabel("Email or phone").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("If that account exists")).toBeVisible();

  // The outbox worker drains roughly every second; poll Mailpit for the mail.
  let link = "";
  await expect
    .poll(
      async () => {
        const list = (await (await request.get(`${MAILPIT}/api/v1/search?query=to:${email}`)).json()) as {
          messages: { ID: string }[];
        };
        if (!list.messages?.length) return "";
        const message = (await (await request.get(`${MAILPIT}/api/v1/message/${list.messages[0]!.ID}`)).json()) as {
          Text: string;
        };
        link = /http\S+\/reset-password\?token=\S+/.exec(message.Text)?.[0] ?? "";
        return link;
      },
      { timeout: 20_000, message: "reset email never arrived in Mailpit" },
    )
    .not.toBe("");

  const newPassword = "Lantern-Harbour-Evening-42";
  await page.goto(new URL(link).pathname + new URL(link).search);
  await page.getByLabel("New password", { exact: true }).fill(newPassword);
  await page.getByLabel("Repeat new password").fill(newPassword);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByText("Password has been reset")).toBeVisible();

  // The old password no longer works; the new one does.
  await page.goto("/login");
  await page.getByLabel("Email or phone").fill(email);
  await page.getByLabel("Password").fill(DEV_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("do not match");
  await signIn(page, email, newPassword);
});
