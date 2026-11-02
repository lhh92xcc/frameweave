import { defineConfig } from "@playwright/test";
const port = process.env.FRAMEWEAVE_STUDIO_PORT || "8787";
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 12000 },
  use: { baseURL, trace: "retain-on-failure" },
  webServer: {
    command:
      "FRAMEWEAVE_GATEWAY_PORT=18866 FRAMEWEAVE_DATA=../.local/e2e python3 ../scripts/dev.py serve",
    url: baseURL + "/api/health",
    reuseExistingServer: false,
    timeout: 60000,
  },
  reporter: [["list"]],
});
