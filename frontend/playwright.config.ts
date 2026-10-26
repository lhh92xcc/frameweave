import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 12000 },
  use: { baseURL: "http://127.0.0.1:8787", trace: "retain-on-failure" },
  webServer: {
    command: "FRAMEWEAVE_DATA=../.local/e2e python3 ../scripts/dev.py serve",
    url: "http://127.0.0.1:8787/api/health",
    reuseExistingServer: false,
    timeout: 60000,
  },
  reporter: [["list"]],
});
