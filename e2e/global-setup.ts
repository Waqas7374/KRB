import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Seeded accounts start with a forced password change, and a previous run
 * may have changed or locked one. Put them all back to the dev password so
 * every run starts from the same state. The command refuses to run against
 * a production configuration.
 */
export default function globalSetup(): void {
  execSync("docker compose exec -T api python -m app.seeds --reset-dev-passwords", {
    cwd: root,
    stdio: "inherit",
  });
}
