/**
 * HTTP client.
 *
 * One place that knows how to talk to the API: base URL, auth header,
 * idempotency keys, and the RFC 9457 error contract. Feature modules import
 * `api` and never touch `fetch` directly, so retry, auth refresh and error
 * shaping stay consistent.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** A field-level failure as returned by the backend's problem document. */
export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface ProblemDocument {
  type: string;
  title: string;
  status: number;
  detail?: string | null;
  instance?: string;
  request_id?: string;
  errors?: FieldError[];
  [key: string]: unknown;
}

/**
 * Thrown for every non-2xx response.
 *
 * Carries the parsed problem document so forms can map `errors` onto fields
 * and support can quote `requestId`.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDocument;
  readonly fieldErrors: FieldError[];
  readonly requestId?: string;

  constructor(status: number, problem: ProblemDocument) {
    super(problem.detail || problem.title || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.fieldErrors = problem.errors ?? [];
    this.requestId = problem.request_id;
  }

  get isAuthError(): boolean {
    return this.status === 401;
  }

  get isPermissionError(): boolean {
    return this.status === 403;
  }

  get isValidationError(): boolean {
    return this.status === 422;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  /** True when retrying the identical request could plausibly succeed. */
  get isRetryable(): boolean {
    return this.status === 429 || this.status >= 500;
  }
}

export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Could not reach the server. Check your connection.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

/** Standard collection envelope — see docs/07-api-specification.md §1. */
export interface Page<T> {
  items: T[];
  page: {
    limit: number;
    offset: number;
    total: number | null;
    has_more: boolean;
    next_cursor: string | null;
  };
  meta: Record<string, unknown>;
}

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions extends Omit<RequestInit, "body"> {
  query?: Record<string, QueryValue | QueryValue[]>;
  body?: unknown;
  /** Required by the backend on financial and inventory-moving POSTs. */
  idempotencyKey?: string;
  /** Optimistic concurrency: the `version` of the record being edited. */
  ifMatch?: string;
  /** Return the raw body (a file) instead of parsing JSON. */
  asBlob?: boolean;
}

let accessToken: string | null = null;
let onUnauthorized: (() => void) | null = null;
let refreshHandler: (() => Promise<boolean>) | null = null;
let refreshInFlight: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/**
 * Registers the function that obtains a new access token. It resolves `true`
 * when a new token has been set. The client calls it on a 401 and retries the
 * original request once.
 */
export function setRefreshHandler(handler: (() => Promise<boolean>) | null): void {
  refreshHandler = handler;
}

/**
 * Single-flight: when five queries hit an expired token at once, exactly one
 * refresh request is made. Refresh tokens rotate with reuse detection on the
 * server, so a second concurrent refresh would present an already-spent token
 * and revoke the whole session chain.
 */
function refreshOnce(): Promise<boolean> {
  if (!refreshHandler) return Promise.resolve(false);
  refreshInFlight ??= refreshHandler()
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

/** Endpoints where a 401 means "wrong credentials", not "token expired". */
const NO_REFRESH_PATHS = ["/auth/login", "/auth/refresh", "/auth/password/"];

/**
 * The page's origin, used to resolve a relative VITE_API_BASE_URL. The
 * production image is built with `/api/v1` (API behind the same origin), and
 * `new URL("/api/v1/...")` without a base throws — which once meant the
 * production build could not make a single request.
 */
function pageOrigin(): string {
  return typeof window === "undefined" ? "http://localhost" : window.location.origin;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(
    path.startsWith("http") ? path : `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`,
    pageOrigin(),
  );
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      // Repeated keys mean OR, matching the backend's filter grammar.
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item !== undefined && item !== null && item !== "") {
            url.searchParams.append(key, String(item));
          }
        }
      } else {
        url.searchParams.append(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseProblem(response: Response): Promise<ProblemDocument> {
  try {
    const body = (await response.json()) as ProblemDocument;
    if (body && typeof body === "object" && "status" in body) return body;
    return { type: "about:blank", title: response.statusText, status: response.status };
  } catch {
    return {
      type: "about:blank",
      title: response.statusText || "Request failed",
      status: response.status,
    };
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return send<T>(path, options, true);
}

async function send<T>(path: string, options: RequestOptions, mayRefresh: boolean): Promise<T> {
  const { query, body, idempotencyKey, ifMatch, asBlob, headers, ...init } = options;

  const requestHeaders = new Headers(headers);
  if (body !== undefined && !(body instanceof FormData)) {
    requestHeaders.set("Content-Type", "application/json");
  }
  if (accessToken) requestHeaders.set("Authorization", `Bearer ${accessToken}`);
  if (idempotencyKey) requestHeaders.set("Idempotency-Key", idempotencyKey);
  if (ifMatch) requestHeaders.set("If-Match", ifMatch);

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      ...init,
      headers: requestHeaders,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch (cause) {
    throw new NetworkError(cause);
  }

  if (response.status === 401) {
    const refreshable = mayRefresh && !NO_REFRESH_PATHS.some((p) => path.startsWith(p));
    // Retry once with a fresh token. The retried request reuses the same
    // idempotency key, so a POST that did land cannot be applied twice.
    if (refreshable && (await refreshOnce())) {
      return send<T>(path, options, false);
    }
    if (refreshable || !mayRefresh) onUnauthorized?.();
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseProblem(response));
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  if (asBlob) return (await response.blob()) as T;
  return (await response.json()) as T;
}

/** Generate an idempotency key for a mutation the user is about to submit. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "DELETE" }),
};

/**
 * Infrastructure endpoints live outside /api/v1, so they need the bare origin.
 */
export interface ReadyReport {
  status: "ready" | "not_ready";
  checked_at: string;
  components: { name: string; ready: boolean; detail: string | null }[];
}

export const systemApi = {
  health: () => request<{ status: string; app: string; environment: string }>(rootUrl("/health")),

  /**
   * `/ready` answers 503 when a dependency is down but still returns a full
   * report body. That is not an error to surface — it is the answer — so this
   * one endpoint reads the body on both 200 and 503.
   */
  ready: async (): Promise<ReadyReport> => {
    let response: Response;
    try {
      response = await fetch(rootUrl("/ready"));
    } catch (cause) {
      throw new NetworkError(cause);
    }
    if (response.ok || response.status === 503) {
      return (await response.json()) as ReadyReport;
    }
    throw new ApiError(response.status, await parseProblem(response));
  },

  version: () =>
    request<{ app: string; version: string; git_sha: string; environment: string }>(
      rootUrl("/version"),
    ),
};

function rootUrl(path: string): string {
  const base = new URL(BASE_URL, pageOrigin());
  return `${base.origin}${path}`;
}

/** Exposed for tests only. */
export const __testing = { buildUrl, rootUrl };
