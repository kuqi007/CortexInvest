import { join } from "path";
import Database from "better-sqlite3";

const DATA_DIR = join(process.cwd(), "..", "src", "data");

export const CONFIG_DB_PATH = join(DATA_DIR, "config.db");
export const TRADING_DB_PATH = join(DATA_DIR, "trading.db");

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
