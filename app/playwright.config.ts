import { defineConfig, devices } from "@playwright/test";

/**
 * Visual regression for the desktop UI in fixture mode (deterministic data,
 * no core). Baselines live next to the spec; update with `pnpm test:visual -u`.
 */
export default defineConfig({
  testDir: "./tests/visual",
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}-{projectName}{ext}",
  fullyParallel: true,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:1429", colorScheme: "dark", deviceScaleFactor: 1 },
  expect: { toHaveScreenshot: { maxDiffPixels: 50, animations: "disabled" } },
  projects: [
    { name: "1440x900", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "1280x800", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
    { name: "1920x1080", use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } } },
    { name: "1024x700", use: { ...devices["Desktop Chrome"], viewport: { width: 1024, height: 700 } } },
    // Light theme (secondary): selected through the persisted preference, not the OS scheme.
    { name: "1440x900-light", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "1024x700-light", use: { ...devices["Desktop Chrome"], viewport: { width: 1024, height: 700 } } },
  ],
  webServer: {
    command: "npx vite --port 1429 --strictPort",
    url: "http://localhost:1429",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
