import { create } from "zustand";

import { api, setAccessToken, setRefreshHandler, setUnauthorizedHandler } from "@/lib/api";
import type { MeResponse, TokenResponse } from "@/types/models";

/**
 * authStore — one of the three sanctioned global stores (docs/08 §1).
 *
 * Token handling:
 * - The access token lives only in memory (module variable in lib/api).
 * - The refresh token is kept in localStorage so a reload does not sign the
 *   user out. That makes it readable by any script on the origin; the backend
 *   takes the refresh token in the request body rather than an httpOnly
 *   cookie, so there is no safer place for it today. Mitigations: rotation
 *   with reuse detection server-side, a strict CSP in the nginx config, and
 *   no third-party scripts. Moving to a cookie is recorded in
 *   docs/15-phase-1-frontend-delivery.md as an open decision.
 */

const REFRESH_KEY = "krb.refresh";

type Status = "booting" | "anonymous" | "authenticated";

interface AuthState {
  status: Status;
  me: MeResponse | null;
  /** Set when the last sign-out was caused by an expired or revoked session. */
  sessionExpired: boolean;
  boot: () => Promise<void>;
  login: (identifier: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reloadMe: () => Promise<void>;
}

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function writeRefresh(token: string | null): void {
  try {
    if (token) localStorage.setItem(REFRESH_KEY, token);
    else localStorage.removeItem(REFRESH_KEY);
  } catch {
    // Private mode: the session simply will not survive a reload.
  }
}

function adoptTokens(tokens: TokenResponse): void {
  setAccessToken(tokens.access_token);
  writeRefresh(tokens.refresh_token);
}

async function refreshTokens(): Promise<boolean> {
  const refreshToken = readRefresh();
  if (!refreshToken) return false;
  try {
    const tokens = await api.post<TokenResponse>("/auth/refresh", { refresh_token: refreshToken });
    adoptTokens(tokens);
    return true;
  } catch {
    writeRefresh(null);
    return false;
  }
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: "booting",
  me: null,
  sessionExpired: false,

  boot: async () => {
    if (!(await refreshTokens())) {
      set({ status: "anonymous", me: null });
      return;
    }
    try {
      const me = await api.get<MeResponse>("/auth/me");
      set({ status: "authenticated", me });
    } catch {
      set({ status: "anonymous", me: null });
    }
  },

  login: async (identifier, password) => {
    const tokens = await api.post<TokenResponse>("/auth/login", { identifier, password });
    adoptTokens(tokens);
    const me = await api.get<MeResponse>("/auth/me");
    set({ status: "authenticated", me, sessionExpired: false });
  },

  logout: async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      // Signing out locally must succeed even if the server is unreachable.
    }
    setAccessToken(null);
    writeRefresh(null);
    set({ status: "anonymous", me: null, sessionExpired: false });
  },

  reloadMe: async () => {
    if (get().status !== "authenticated") return;
    const me = await api.get<MeResponse>("/auth/me");
    set({ me });
  },
}));

setRefreshHandler(refreshTokens);
setUnauthorizedHandler(() => {
  if (useAuthStore.getState().status !== "authenticated") return;
  setAccessToken(null);
  writeRefresh(null);
  useAuthStore.setState({ status: "anonymous", me: null, sessionExpired: true });
});
