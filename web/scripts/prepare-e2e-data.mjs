import Database from "better-sqlite3";
import { existsSync, mkdirSync, rmSync } from "fs";
import { join, resolve, sep } from "path";
import { fileURLToPath } from "url";

const scriptDir = resolve(fileURLToPath(new URL(".", import.meta.url)));
const webRoot = resolve(scriptDir, "..");
const repoRoot = resolve(webRoot, "..");

const defaultTargetDir = resolve(webRoot, ".e2e-data");
const sourceDir = resolve(process.env.AI_INVESTOR_E2E_SOURCE_DATA_DIR || join(repoRoot, "src", "data"));
const targetDir = resolve(process.env.AI_INVESTOR_E2E_DATA_DIR || defaultTargetDir);
const repoDataDir = resolve(repoRoot, "src", "data");
const allowCustomTarget = process.env.AI_INVESTOR_E2E_ALLOW_CUSTOM_DATA_DIR === "1";

if (sourceDir === targetDir) {
  throw new Error("E2E source and target data directories must be different");
}
if (!allowCustomTarget && targetDir !== defaultTargetDir) {
  throw new Error("Refusing custom E2E target without AI_INVESTOR_E2E_ALLOW_CUSTOM_DATA_DIR=1");
}
if (
  targetDir === repoDataDir ||
  targetDir.startsWith(`${repoDataDir}${sep}`) ||
  repoDataDir.startsWith(`${targetDir}${sep}`)
) {
  throw new Error(`Refusing to use repo runtime data as E2E target: ${targetDir}`);
}

function requireDb(name) {
  const dbPath = join(sourceDir, name);
  if (!existsSync(dbPath)) {
    throw new Error(`Missing source database: ${dbPath}`);
  }
  return dbPath;
}

async function backupDb(sourcePath, targetPath) {
  const db = new Database(sourcePath, { readonly: true, fileMustExist: true });
  try {
    await db.backup(targetPath);
  } finally {
    db.close();
  }
}

rmSync(targetDir, { recursive: true, force: true });
mkdirSync(targetDir, { recursive: true });
mkdirSync(join(targetDir, "audit"), { recursive: true });
mkdirSync(join(targetDir, "archive"), { recursive: true });
mkdirSync(join(targetDir, "stock_news"), { recursive: true });

await backupDb(requireDb("config.db"), join(targetDir, "config.db"));
await backupDb(requireDb("trading.db"), join(targetDir, "trading.db"));

console.log(`Prepared isolated E2E data at ${targetDir}`);
