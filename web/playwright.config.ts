import { defineConfig } from "@playwright/test";
import { join } from "path";

const e2eDataDir = process.env.AI_INVESTOR_E2E_DATA_DIR ?? join(process.cwd(), ".e2e-data");

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL: "http://localhost:3120",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    navigationTimeout: 15_000,
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
  webServer: {
    command: "node scripts/prepare-e2e-data.mjs && npm run build && npm run start",
    port: 3120,
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      AI_INVESTOR_DATA_DIR: e2eDataDir,
      AI_INVESTOR_E2E_DATA_DIR: e2eDataDir,
    },
  },
});
