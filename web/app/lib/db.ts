import { join } from "path";
import Database from "better-sqlite3";

const DATA_DIR = join(process.cwd(), "..", "src", "data");

function dbPath(envName: string, defaultPath: string) {
  const override = process.env[envName];
  if (!override) return defaultPath;
  const allowOverride = process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE === "1";
  const isTestRuntime = process.env.NODE_ENV === "test" || process.env.VITEST;
  if (allowOverride && isTestRuntime) return override;
  throw new Error(`${envName} requires AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE=1 in test`);
}

export const CONFIG_DB_PATH = dbPath("AI_INVESTOR_CONFIG_DB_PATH", join(DATA_DIR, "config.db"));
export const TRADING_DB_PATH = dbPath("AI_INVESTOR_TRADING_DB_PATH", join(DATA_DIR, "trading.db"));

// Backward compat — remove after all routes migrated
export const SIM_DB_PATH = TRADING_DB_PATH;

export function openConfigDb(readonly = false) {
  const db = new Database(CONFIG_DB_PATH, { readonly });
  if (!readonly) db.pragma("journal_mode = DELETE");
  db.pragma("busy_timeout = 15000");
  return db;
}

export function openTradingDb(readonly = false) {
  const db = new Database(TRADING_DB_PATH, { readonly });
  if (!readonly) db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 15000");
  return db;
}
