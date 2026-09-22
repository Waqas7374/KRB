import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError, api, setAccessToken, setUnauthorizedHandler } from "./api";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("api client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken(null);
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("returns the parsed body on success", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: "1", name: "Shree Stone" }));
    await expect(api.get("/vendors/1")).resolves.toEqual({ id: "1", name: "Shree Stone" });
  });

  it("sends the bearer token once set", async () => {
    setAccessToken("token-abc");
    fetchMock.mockResolvedValue(jsonResponse({}));
    await api.get("/vendors");

    const headers = (fetchMock.mock.calls[0]![1] as RequestInit).headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-abc");
  });

  it("passes the idempotency key through on mutations", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}, 201));
    await api.post("/purchase-orders", { total: "1000.0000" }, { idempotencyKey: "key-1" });

    const headers = (fetchMock.mock.calls[0]![1] as RequestInit).headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("key-1");
  });

  it("serialises array filters as repeated query parameters", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }));
    await api.get("/purchase-orders", { query: { status: ["APPROVED", "SENT"], limit: 50 } });

    const url = fetchMock.mock.calls[0]![0] as string;
    expect(url).toContain("status=APPROVED");
    expect(url).toContain("status=SENT");
    expect(url).toContain("limit=50");
  });

  it("omits empty query values rather than sending blanks", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }));
    await api.get("/vendors", { query: { q: "", status: undefined, page: 2 } });

    const url = fetchMock.mock.calls[0]![0] as string;
    expect(url).not.toContain("q=");
    expect(url).not.toContain("status=");
    expect(url).toContain("page=2");
  });

  it("throws ApiError carrying the field errors from a problem document", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          type: "https://errors.krb-erp/validation",
          title: "Validation failed",
          status: 422,
          detail: "One or more fields are invalid.",
          request_id: "req-99",
          errors: [{ field: "items.0.quantity", code: "greater_than", message: "must be > 0" }],
        },
        422,
      ),
    );

    const error = await api.post("/purchase-orders", {}).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.isValidationError).toBe(true);
    expect(apiError.requestId).toBe("req-99");
    expect(apiError.fieldErrors[0]?.field).toBe("items.0.quantity");
  });

  it("notifies the unauthorized handler on 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockResolvedValue(jsonResponse({ status: 401, title: "Unauthenticated" }, 401));

    await api.get("/auth/me").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("marks 5xx and 429 as retryable, and 4xx as not", async () => {
    const cases: [number, boolean][] = [
      [400, false],
      [403, false],
      [422, false],
      [429, true],
      [500, true],
      [503, true],
    ];
    for (const [status, retryable] of cases) {
      fetchMock.mockResolvedValue(jsonResponse({ status, title: "x" }, status));
      const error = (await api.get("/x").catch((e: unknown) => e)) as ApiError;
      expect(error.isRetryable, `status ${status}`).toBe(retryable);
    }
  });

  it("wraps transport failures as NetworkError", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.get("/vendors")).rejects.toBeInstanceOf(NetworkError);
  });

  it("returns undefined for 204 responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.delete("/notifications/1")).resolves.toBeUndefined();
  });
});
