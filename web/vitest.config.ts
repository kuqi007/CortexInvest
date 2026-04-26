import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["app/**/*.test.ts"],
    exclude: ["e2e/**", "tests/**", "node_modules/**"],
  },
});
