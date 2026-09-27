import * as SQLite from "expo-sqlite";

import type { SqlDatabase } from "../core/store";

/** The phone's `SqlDatabase`, over expo-sqlite. The SQL itself lives in `core/store.ts`. */
export async function openDatabase(name = "site-ledger.db"): Promise<SqlDatabase> {
  const db = await SQLite.openDatabaseAsync(name);
  // Write-ahead logging: a save is durable the moment it returns, and readers are not blocked.
  await db.execAsync("PRAGMA journal_mode = WAL;");
  return {
    exec: (sql) => db.execAsync(sql),
    run: async (sql, params = []) => {
      await db.runAsync(sql, params);
    },
    all: <T>(sql: string, params: (string | number | null)[] = []) => db.getAllAsync<T>(sql, params),
    transaction: async <T>(fn: () => Promise<T>): Promise<T> => {
      let result: T | undefined;
      await db.withTransactionAsync(async () => {
        result = await fn();
      });
      return result as T;
    },
  };
}
