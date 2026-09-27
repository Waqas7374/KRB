import type { SqlDatabase, SqlValue } from "../core/store";

// Fetched from the runtime directly: the bundler that runs the tests does not know this newer
// built-in module and would try to resolve it as a package.
const { DatabaseSync } = process.getBuiltinModule("node:sqlite");

/** The same `SqlDatabase` the phone uses, over Node's built-in SQLite, for tests. */
export function nodeDb(path = ":memory:"): SqlDatabase {
  const db = new DatabaseSync(path);
  return {
    exec: (sql) => {
      db.exec(sql);
      return Promise.resolve();
    },
    run: (sql, params: SqlValue[] = []) => {
      db.prepare(sql).run(...params);
      return Promise.resolve();
    },
    all: <T>(sql: string, params: SqlValue[] = []) =>
      Promise.resolve(db.prepare(sql).all(...params) as T[]),
    transaction: async <T>(fn: () => Promise<T>): Promise<T> => {
      db.exec("BEGIN");
      try {
        const result = await fn();
        db.exec("COMMIT");
        return result;
      } catch (err) {
        db.exec("ROLLBACK");
        throw err;
      }
    },
  };
}
