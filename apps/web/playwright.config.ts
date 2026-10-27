import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 15000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "line",
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:5173" },
  webServer: { command: "node node_modules/vite/bin/vite.js --host 127.0.0.1", url: "http://127.0.0.1:5173", reuseExistingServer: true },
});
