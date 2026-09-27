import Constants from "expo-constants";
import { Platform } from "react-native";

import { uuidv7 } from "../core/ids";
import type { DeviceInfo } from "../core/session";
import { secureStorage } from "./secure";

/**
 * Where the one ERP backend is. Set EXPO_PUBLIC_API_URL when starting Expo; otherwise the
 * Android emulator's address for this machine (it cannot see `localhost`) or, elsewhere,
 * localhost. On a real phone, use this machine's LAN address.
 */
export const API_URL: string =
  process.env.EXPO_PUBLIC_API_URL ??
  (Platform.OS === "android" ? "http://10.0.2.2:8000/api/v1" : "http://localhost:8000/api/v1");

const DEVICE_KEY = "krb.device_uid";

/** Generated on first launch and kept: this is how head office recognises (and can revoke) a phone. */
export async function loadDevice(): Promise<DeviceInfo> {
  let uid = await secureStorage.get(DEVICE_KEY);
  if (!uid) {
    uid = uuidv7();
    await secureStorage.set(DEVICE_KEY, uid);
  }
  return {
    device_uid: uid,
    platform: Platform.OS === "ios" ? "IOS" : "ANDROID",
    os_version: String(Platform.Version),
    app_version: Constants.expoConfig?.version ?? "0.0.0",
  };
}
