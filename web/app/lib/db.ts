import { join } from "path";
import Database from "better-sqlite3";

function resolveDataDir(): string {
  // 1. Check env var first (absolute path)
  const envOverride = process.env.AI_INVESTOR_DATA_DIR;
  if (envOverride) return envOverride;

  const repoRoot = join(process.cwd(), "..");

  // 2. Try <repo>/src/data (symlink — may or may not exist)
  // 3. Fall back to <repo>/data
  const srcDataPath = join(repoRoot, "src", "data");
  const repoDataPath = join(repoRoot, "data");

  try {
    const fs = require("fs") as typeof import("fs");
    if (fs.existsSync(srcDataPath)) return srcDataPath;
  } catch {
    // fs not available (edge runtime) — assume src/data
  }
  return repoDataPath;
}

const DATA_DIR = resolveDataDir();

function dbPath(envName: string, defaultPath: string) {
  const override = process.env[envName];
  if (!override) return defaultPath;
  // Test runtime needs flag to prevent accidental prod override; dev always allowed
  if (process.env.NODE_ENV === "test" || process.env.VITEST) {
    if (process.env.AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE !== "1") {
      throw new Error(`${envName} requires AI_INVESTOR_ALLOW_TEST_DB_OVERRIDE=1 in test`);
    }
  }
  return override;
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
