import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 15_000,
  expect: { timeout: 5_000 },
  retries: 0,
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    // Optional installed Edge runtime; default remains Playwright Chromium.
    channel: process.env.PLAYWRIGHT_CHANNEL === "msedge" ? "msedge" : undefined,
    baseURL: "http://127.0.0.1:5174",
  },
  webServer: {
    command: "npm run build && node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5174 --strictPort",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
