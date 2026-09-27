import * as SecureStore from "expo-secure-store";

import type { SecureStorage } from "../core/session";

/** The refresh token belongs in the Keychain / Keystore, never in the database. */
export const secureStorage: SecureStorage = {
  get: (key) => SecureStore.getItemAsync(key),
  set: (key, value) => SecureStore.setItemAsync(key, value),
  delete: (key) => SecureStore.deleteItemAsync(key),
};
