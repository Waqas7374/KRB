import { execFileSync } from "node:child_process";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const DEV_PASSWORD = "KrbDev!Passw0rd";
export const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";

export const USERS = {
  admin: "admin@krb.example",
  procurement: "procurement@krb.example",
  auditor: "auditor@krb.example",
  siteStaff: "staff.gvh1@krb.example",
  accounts: "accounts@krb.example",
  finance: "finance@krb.example",
  ceo: "ceo@krb.example",
} as const;

/** Sign in through the real login screen and wait for the shell. */
export async function signIn(page: Page, email: string, password = DEV_PASSWORD): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email or phone").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /^Welcome,/ })).toBeVisible();
}

/**
 * Run SQL in the database container and return trimmed rows. Test-only: the
 * values interpolated into queries are ones the test generated itself.
 */
export function sql(query: string): string[] {
  const out = execFileSync(
    "docker",
    ["exec", "krb-db", "psql", "-U", "krb", "-d", "krb_erp", "-At", "-c", query],
    { encoding: "utf8" },
  );
  return out
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** An access token obtained directly from the API, for API-level assertions. */
export async function apiToken(request: APIRequestContext, email: string): Promise<string> {
  const response = await request.post(`${API}/auth/login`, {
    data: { identifier: email, password: DEV_PASSWORD },
  });
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as { access_token: string }).access_token;
}

/** A unique, valid NTN (1234567-8) so reruns never collide. */
export function uniqueNtn(): string {
  const digits = String(Date.now()).slice(-7);
  return `${digits}-${Math.floor(Math.random() * 10)}`;
}
