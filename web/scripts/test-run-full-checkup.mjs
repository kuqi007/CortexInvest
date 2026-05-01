import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { assertIsolatedDataDir, assertPortAvailable, parseArgs } from "./run-full-checkup.mjs";

const tmp = mkdtempSync(join(tmpdir(), "ai-investor-checkup-test-"));

try {
  const webRoot = resolve(tmp, "web");
  const repoRoot = resolve(tmp);
  const runtimeData = resolve(repoRoot, "src", "data");
  const isolatedData = resolve(webRoot, ".e2e-runs", "run-1", "data");

  assert.deepEqual(parseArgs([]), { rounds: 5, keepFailed: true });
  assert.deepEqual(parseArgs(["--rounds", "2", "--discard-failed"]), { rounds: 2, keepFailed: false });
  assert.throws(() => parseArgs(["--rounds", "0"]), /positive integer/);

  assert.doesNotThrow(() => assertIsolatedDataDir({ dataDir: isolatedData, repoRoot }));
  assert.throws(() => assertIsolatedDataDir({ dataDir: runtimeData, repoRoot }), /Refusing/);
  assert.throws(() => assertIsolatedDataDir({ dataDir: resolve(runtimeData, "nested"), repoRoot }), /Refusing/);

  const occupiedServer = createServer();
  await new Promise((resolve) => occupiedServer.listen(0, "127.0.0.1", resolve));
  const occupiedPort = occupiedServer.address().port;
  await assert.rejects(() => assertPortAvailable(occupiedPort));
  await new Promise((resolve) => occupiedServer.close(resolve));
  await assert.doesNotReject(() => assertPortAvailable(occupiedPort));
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

console.log("run-full-checkup helpers ok");
