import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync, copyFileSync, createWriteStream } from "node:fs";
import { createServer } from "node:net";
import { join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDir = resolve(fileURLToPath(new URL(".", import.meta.url)));
const webRoot = resolve(scriptDir, "..");
const repoRoot = resolve(webRoot, "..");
const latestScreenshotsDir = join(webRoot, "screenshots", "latest");
const host = "127.0.0.1";
const port = 3120;
const baseUrl = `http://${host}:${port}`;

export function parseArgs(argv) {
  const args = { rounds: 5, keepFailed: true };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--rounds") {
      const value = Number(argv[i + 1]);
      if (!Number.isInteger(value) || value <= 0) {
        throw new Error("--rounds must be a positive integer");
      }
      args.rounds = value;
      i += 1;
    } else if (arg === "--discard-failed") {
      args.keepFailed = false;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

export function assertIsolatedDataDir({ dataDir, repoRoot }) {
  const resolvedDataDir = resolve(dataDir);
  const runtimeDataDir = resolve(repoRoot, "src", "data");
  if (
    resolvedDataDir === runtimeDataDir ||
    resolvedDataDir.startsWith(`${runtimeDataDir}${sep}`) ||
    runtimeDataDir.startsWith(`${resolvedDataDir}${sep}`)
  ) {
    throw new Error(`Refusing to run E2E against runtime data dir: ${resolvedDataDir}`);
  }
}

export async function assertPortAvailable(port) {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.once("listening", resolve);
    server.listen(port, "127.0.0.1");
  }).finally(() => {
    server.close();
  });
}

function run(command, args, { cwd = webRoot, env = {}, logFile, timeoutMs } = {}) {
  const output = spawnSync(command, args, {
    cwd,
    env: { ...process.env, ...env },
    encoding: "utf8",
    timeout: timeoutMs,
  });
  const text = `${output.stdout || ""}${output.stderr || ""}`;
  if (logFile) {
    const stream = createWriteStream(logFile, { flags: "a" });
    stream.write(text);
    stream.end();
  }
  if (output.error) {
    throw new Error(`${command} ${args.join(" ")} failed: ${output.error.message}\n${text.slice(-2000)}`);
  }
  if (output.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with ${output.status}\n${text.slice(-2000)}`);
  }
  return text;
}

async function waitForServer(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
      lastError = `HTTP ${res.status}`;
    } catch (e) {
      lastError = e.message;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Server did not become ready at ${url}: ${lastError}`);
}

async function terminateServer(server) {
  if (!server || server.exitCode !== null || server.signalCode !== null) return;
  const pid = server.pid;
  const sendSignal = (signal) => {
    try {
      process.kill(-pid, signal);
    } catch {
      try { server.kill(signal); } catch { /* already stopped */ }
    }
  };
  const waitForExit = (timeoutMs) => new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    server.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });

  sendSignal("SIGTERM");
  if (!(await waitForExit(2000))) {
    sendSignal("SIGKILL");
    await waitForExit(1000);
  }
}

function copyCheckupScreenshots(targetDir) {
  mkdirSync(targetDir, { recursive: true });
  const sourceDir = join(webRoot, "screenshots");
  for (const name of readdirSync(sourceDir)) {
    if (name.startsWith("checkup_") && name.endsWith(".png")) {
      copyFileSync(join(sourceDir, name), join(targetDir, name));
    }
  }
}

function cleanRootCheckupScreenshots() {
  const sourceDir = join(webRoot, "screenshots");
  for (const name of readdirSync(sourceDir)) {
    if (name.startsWith("checkup_") && name.endsWith(".png")) {
      rmSync(join(sourceDir, name), { force: true });
    }
  }
}

function archiveLatestScreenshots(sourceDir) {
  rmSync(latestScreenshotsDir, { recursive: true, force: true });
  mkdirSync(latestScreenshotsDir, { recursive: true });
  for (const name of readdirSync(sourceDir)) {
    if (name.endsWith(".png")) {
      copyFileSync(join(sourceDir, name), join(latestScreenshotsDir, name));
    }
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const runId = new Date().toISOString().replace(/[:.]/g, "-");
  const runRoot = join(webRoot, ".e2e-runs", runId);
  const dataDir = join(runRoot, "data");
  const logsDir = join(runRoot, "logs");
  const roundsDir = join(runRoot, "rounds");
  const env = {
    PLAYWRIGHT_BROWSERS_PATH: process.env.AI_INVESTOR_PLAYWRIGHT_BROWSERS_PATH || join(process.env.HOME || "", "Library", "Caches", "ms-playwright"),
    AI_INVESTOR_E2E_BASE_URL: baseUrl,
    AI_INVESTOR_DATA_DIR: dataDir,
    AI_INVESTOR_E2E_DATA_DIR: dataDir,
    AI_INVESTOR_E2E_ALLOW_CUSTOM_DATA_DIR: "1",
    AI_INVESTOR_ALLOW_CONFIG_MUTATION_E2E: "1",
  };

  assertIsolatedDataDir({ dataDir, repoRoot });
  mkdirSync(logsDir, { recursive: true });
  mkdirSync(roundsDir, { recursive: true });

  let server;
  let success = false;
  let lastRoundDir = "";
  let exiting = false;

  const cleanupAndExit = (code, error) => {
    if (error) console.error(error);
    if (exiting) process.exit(code);
    exiting = true;
    terminateServer(server).finally(() => process.exit(code));
  };
  process.once("SIGINT", () => cleanupAndExit(130));
  process.once("SIGTERM", () => cleanupAndExit(143));
  process.once("uncaughtException", (error) => cleanupAndExit(1, error));
  process.once("unhandledRejection", (error) => cleanupAndExit(1, error));

  try {
    console.log(`Preparing isolated E2E data: ${dataDir}`);
    run("node", ["scripts/prepare-e2e-data.mjs"], { env, logFile: join(logsDir, "prepare.log"), timeoutMs: 30_000 });

    console.log("Building frontend");
    run("npm", ["run", "build"], { env, logFile: join(logsDir, "build.log"), timeoutMs: 120_000 });

    await assertPortAvailable(port);
    console.log("Starting isolated production server");
    const serverLog = createWriteStream(join(logsDir, "server.log"), { flags: "a" });
    server = spawn(join(webRoot, "node_modules", ".bin", "next"), ["start", "-H", host, "-p", String(port)], {
      cwd: webRoot,
      env: { ...process.env, ...env },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    server.stdout.pipe(serverLog);
    server.stderr.pipe(serverLog);
    await waitForServer(`${baseUrl}/`);

    for (let round = 1; round <= args.rounds; round += 1) {
      console.log(`Running checkup round ${round}/${args.rounds}`);
      cleanRootCheckupScreenshots();
      const logFile = join(logsDir, `round-${round}.log`);
      const output = run("node", ["screenshots/test_full_checkup.mjs"], { env, logFile, timeoutMs: 120_000 });
      const summary = output.match(/CHECKUP COMPLETE: .*/)?.[0] || "missing summary";
      console.log(summary);
      lastRoundDir = join(roundsDir, `round-${round}`);
      copyCheckupScreenshots(lastRoundDir);
    }

    archiveLatestScreenshots(lastRoundDir);
    cleanRootCheckupScreenshots();
    success = true;
    console.log(`Latest screenshots kept at ${latestScreenshotsDir}`);
  } finally {
    await terminateServer(server);
    if (success || !args.keepFailed) {
      rmSync(runRoot, { recursive: true, force: true });
    } else if (existsSync(runRoot)) {
      console.error(`Failed run preserved at ${runRoot}`);
    }
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
