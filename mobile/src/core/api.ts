/**
 * A small HTTP client for the ERP API: bearer auth, one silent token refresh on a 401, and three
 * kinds of failure the sync engine must tell apart:
 *
 *  - `NetworkError`     nothing came back (offline, timed out): keep everything, try later
 *  - `AuthRequiredError` the session is over: park the queue, ask the person to sign in again
 *  - `ApiError`         the server answered with a refusal, and says why (RFC 9457 problem)
 */

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  rule?: string;
  errors?: { field?: string; code?: string; message?: string }[];
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;
  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
  get retryAfterSeconds(): number | null {
    const n = Number(this.problem.retry_after);
    return Number.isFinite(n) && n > 0 ? n : null;
  }
}

export class NetworkError extends Error {
  constructor(message = "The server could not be reached") {
    super(message);
    this.name = "NetworkError";
  }
}

export class AuthRequiredError extends Error {
  constructor(message = "Sign in again to continue") {
    super(message);
    this.name = "AuthRequiredError";
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  baseUrl: string;
  fetch?: Fetch;
  timeoutMs?: number;
  accessToken: () => string | null;
  /** Called once on a 401. Resolve true when a fresh access token is now available. */
  refresh: () => Promise<boolean>;
}

type Query = Record<string, string | number | undefined | string[]>;

export class ApiClient {
  private readonly fetchImpl: Fetch;
  private readonly timeoutMs: number;

  constructor(private readonly options: ApiClientOptions) {
    this.fetchImpl = options.fetch ?? ((input, init) => fetch(input, init));
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  get<T>(path: string, query?: Query): Promise<T> {
    return this.request<T>("GET", path, { query });
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, { body });
  }

  async request<T>(
    method: string,
    path: string,
    { query, body }: { query?: Query | undefined; body?: unknown } = {},
  ): Promise<T> {
    const url = this.url(path, query);
    let response = await this.send(method, url, body);
    if (response.status === 401) {
      // The access token lapsed while the phone was in a pocket: refresh once, quietly.
      if (!(await this.options.refresh())) throw new AuthRequiredError();
      response = await this.send(method, url, body);
      if (response.status === 401) throw new AuthRequiredError();
    }
    if (!response.ok) throw new ApiError(response.status, await problemOf(response));
    return (response.status === 204 ? undefined : await response.json()) as T;
  }

  private url(path: string, query?: Query): string {
    const base = this.options.baseUrl.replace(/\/+$/, "");
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value === undefined) continue;
      for (const v of Array.isArray(value) ? value : [value]) params.append(key, String(v));
    }
    const qs = params.toString();
    return `${base}${path}${qs ? `?${qs}` : ""}`;
  }

  private async send(method: string, url: string, body: unknown): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = this.options.accessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    try {
      return await this.fetchImpl(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch {
      // fetch rejects only when nothing came back at all: no signal, no route, a timeout.
      throw new NetworkError();
    } finally {
      clearTimeout(timer);
    }
  }
}

export async function problemOf(response: Response): Promise<Problem> {
  try {
    return (await response.json()) as Problem;
  } catch {
    return { status: response.status, title: response.statusText };
  }
}
