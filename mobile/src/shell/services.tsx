import NetInfo from "@react-native-community/netinfo";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AppState } from "react-native";

import { ApiClient } from "../core/api";
import { Outbox } from "../core/outbox";
import { loadCatalogue, type Catalogue } from "../core/reference";
import { Session, type Profile } from "../core/session";
import { LocalStore } from "../core/store";
import { SyncEngine } from "../core/sync";
import type { LocalDelivery, SyncStatus } from "../core/types";
import { API_URL, loadDevice } from "../platform/config";
import { openDatabase } from "../platform/expo-db";
import { secureStorage } from "../platform/secure";

/**
 * Everything the screens share, built once at start-up. The queue and the local database do
 * not depend on the session: signing out or an expired login never touches what is waiting to
 * be sent (docs/06 §7).
 */
export interface Services {
  store: LocalStore;
  outbox: Outbox;
  engine: SyncEngine;
  session: Session;
}

export type Auth = "loading" | "signed_out" | "signed_in";

interface AppValue {
  services: Services | null;
  auth: Auth;
  profile: Profile | null;
  status: SyncStatus | null;
  /** Bumped whenever local data may have changed, so lists reload. */
  version: number;
  signIn(identifier: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  syncNow(): Promise<void>;
  changed(): void;
}

const Ctx = createContext<AppValue | null>(null);

const FOREGROUND_SYNC_MS = 60_000;
const FULL_REFRESH_MS = 24 * 3600 * 1000;

export function AppProvider({ children }: { children: ReactNode }) {
  const [services, setServices] = useState<Services | null>(null);
  const [auth, setAuth] = useState<Auth>("loading");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [version, setVersion] = useState(0);
  const changed = useCallback(() => setVersion((v) => v + 1), []);
  const authRef = useRef<Auth>("loading");
  authRef.current = auth;

  // -- Start-up ---------------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const store = new LocalStore(await openDatabase());
      await store.migrate();
      const device = await loadDevice();
      const session = new Session(API_URL, secureStorage, device);
      const api = new ApiClient({
        baseUrl: API_URL,
        accessToken: session.accessToken,
        refresh: session.refresh,
      });
      const engine = new SyncEngine({
        api,
        store,
        device: { device_id: device.device_uid, app_version: device.app_version, platform: device.platform },
        onStatus: (s) => {
          setStatus(s);
          setVersion((v) => v + 1);
        },
      });
      await engine.recover(); // whatever a crash left in flight is simply sent again
      const restored = await session.restore();
      if (cancelled) return;
      setServices({ store, outbox: new Outbox(store), engine, session });
      setProfile(session.profile);
      // Offline at start-up is not signed out: capture works, and the session renews later.
      setAuth(restored === "signed_out" ? "signed_out" : "signed_in");
      setStatus(await engine.status());
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // -- Syncing -------------------------------------------------------------------------------------
  const syncNow = useCallback(async () => {
    if (!services || authRef.current !== "signed_in") return;
    const last = Number(new Date((await services.store.getKv("last_full_refresh")) ?? 0));
    if (Date.now() - last > FULL_REFRESH_MS) {
      await services.store.setKv("last_full_refresh", new Date().toISOString());
      await services.engine.refreshEverything();
    } else {
      await services.engine.run();
    }
    changed();
  }, [services, changed]);

  useEffect(() => {
    if (!services || auth !== "signed_in") return;
    void syncNow();
    const foreground = AppState.addEventListener("change", (state) => {
      if (state === "active") void syncNow();
    });
    // Connectivity regained is the moment that matters most on a site.
    let wasOnline: boolean | null = null;
    const net = NetInfo.addEventListener((s) => {
      const online = Boolean(s.isConnected && s.isInternetReachable !== false);
      if (online && wasOnline === false) void syncNow();
      wasOnline = online;
    });
    const timer = setInterval(() => {
      if (AppState.currentState === "active") void syncNow();
    }, FOREGROUND_SYNC_MS);
    return () => {
      foreground.remove();
      net();
      clearInterval(timer);
    };
  }, [services, auth, syncNow]);

  // A session that has ended parks the queue; the person is asked to sign in again.
  useEffect(() => {
    if (status?.authRequired && auth === "signed_in") setAuth("signed_out");
  }, [status?.authRequired, auth]);

  const value = useMemo<AppValue>(
    () => ({
      services,
      auth,
      profile,
      status,
      version,
      changed,
      syncNow,
      async signIn(identifier, password) {
        if (!services) return;
        const me = await services.session.login(identifier.trim(), password);
        setProfile(me);
        setAuth("signed_in");
      },
      async signOut() {
        if (!services) return;
        await services.session.signOut();
        setAuth("signed_out");
      },
    }),
    [services, auth, profile, status, version, changed, syncNow],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppValue {
  const value = useContext(Ctx);
  if (!value) throw new Error("useApp must be used inside AppProvider");
  return value;
}

/** The site's reference data, reloaded after each sync. */
export function useCatalogue(): Catalogue | null {
  const { services, version } = useApp();
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  useEffect(() => {
    if (!services) return;
    let live = true;
    void loadCatalogue(services.store).then((c) => live && setCatalogue(c));
    return () => {
      live = false;
    };
  }, [services, version]);
  return catalogue;
}

/** The phone's own deliveries, newest first. */
export function useDeliveries(limit = 100): LocalDelivery[] {
  const { services, version } = useApp();
  const [rows, setRows] = useState<LocalDelivery[]>([]);
  useEffect(() => {
    if (!services) return;
    let live = true;
    void services.store.listDeliveries(limit).then((r) => live && setRows(r));
    return () => {
      live = false;
    };
  }, [services, version, limit]);
  return rows;
}
