import { ApiError, NetworkError, problemOf, type Fetch } from "./api";

/**
 * Signing in, staying signed in, and — above all — not losing work when the session ends
 * (docs/06 §5, §7). The refresh token lives in the platform's secure store; the access token
 * lives in memory only. The outbox is independent of all of this: signing out, or an expired
 * session, never touches queued entries.
 */
export interface SecureStorage {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  delete(key: string): Promise<void>;
}

export interface DeviceInfo {
  /** Generated on first launch and kept: the registry's `device_uid`. */
  device_uid: string;
  platform: "ANDROID" | "IOS";
  model?: string;
  os_version?: string;
  app_version: string;
}

export interface Profile {
  id: string;
  full_name: string;
  company_id: string;
  email: string | null;
  role_grants?: { role_code: string; scope_type: string; scope_label: string | null }[];
}

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  user: Profile;
}

const REFRESH_KEY = "krb.refresh_token";
const PROFILE_KEY = "krb.profile";

export type RestoreResult = "signed_in" | "signed_out" | "offline";

export class Session {
  private access: string | null = null;
  private profileCache: Profile | null = null;
  private refreshing: Promise<boolean> | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly storage: SecureStorage,
    private readonly device: DeviceInfo,
    private readonly fetchImpl: Fetch = (input, init) => fetch(input, init),
  ) {}

  accessToken = (): string | null => this.access;
  get profile(): Profile | null {
    return this.profileCache;
  }

  /** Throws `ApiError` for a wrong password or a locked account, `NetworkError` when offline. */
  async login(identifier: string, password: string): Promise<Profile> {
    const tokens = await this.post<TokenResponse>("/auth/login", {
      identifier,
      password,
      device: this.device,
    });
    await this.adopt(tokens);
    return tokens.user;
  }

  /**
   * On app start. Offline is not signed out: with a refresh token on the device the person can
   * still capture deliveries against the cached reference data, and the session is renewed the
   * first time the network lets it.
   */
  async restore(): Promise<RestoreResult> {
    const refresh = await this.storage.get(REFRESH_KEY);
    if (!refresh) return "signed_out";
    const raw = await this.storage.get(PROFILE_KEY);
    this.profileCache = raw ? (JSON.parse(raw) as Profile) : null;
    try {
      return (await this.refresh()) ? "signed_in" : "signed_out";
    } catch (err) {
      if (err instanceof NetworkError) return "offline";
      throw err;
    }
  }

  /**
   * Rotate the refresh token. One at a time: two callers share the one request, because a
   * refresh token is single-use and a second use would revoke the whole chain.
   * Resolves false when the session is genuinely over; throws `NetworkError` when it cannot
   * tell (which is not the same thing).
   */
  refresh = (): Promise<boolean> => {
    this.refreshing ??= this.doRefresh().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  };

  private async doRefresh(): Promise<boolean> {
    const refresh = await this.storage.get(REFRESH_KEY);
    if (!refresh) return false;
    try {
      await this.adopt(await this.post<TokenResponse>("/auth/refresh", { refresh_token: refresh }));
      return true;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        await this.clear();
        return false;
      }
      throw err;
    }
  }

  async signOut(): Promise<void> {
    await this.clear();
  }

  private async adopt(tokens: TokenResponse): Promise<void> {
    this.access = tokens.access_token;
    this.profileCache = tokens.user;
    await this.storage.set(REFRESH_KEY, tokens.refresh_token);
    await this.storage.set(PROFILE_KEY, JSON.stringify(tokens.user));
  }

  private async clear(): Promise<void> {
    this.access = null;
    await this.storage.delete(REFRESH_KEY);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl.replace(/\/+$/, "")}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      throw new NetworkError();
    }
    if (!response.ok) throw new ApiError(response.status, await problemOf(response));
    return (await response.json()) as T;
  }
}
