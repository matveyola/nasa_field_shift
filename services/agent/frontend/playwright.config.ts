import { defineConfig, devices } from "@playwright/test";

// Deterministic by default: agent responses are replayed from e2e/fixtures (recorded from the real
// service), so no backend, LLM key or network is needed. `FS_E2E_LIVE=1` adds the spec that talks
// to the real backends through the Vite proxy (see README › Running the demo).
//
// Browser: PW_CHANNEL (e.g. "chrome", "msedge"); default is the system Edge on Windows, Playwright's
// bundled Chromium elsewhere (`npx playwright install chromium`).
const channel = process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : undefined);

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    channel,
    trace: "retain-on-failure",
    locale: "uk-UA",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], channel, viewport: { width: 1360, height: 860 } } },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
