import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  // These tests drive one shared, stateful backend: creating strategies and
  // running backtests mutates what every other test sees. Run them serially.
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.ALGOFORGE_WEB_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    /* Containers and CI images often carry a browser provisioned for a
     * different Playwright build than the one npm resolved. Without this the
     * whole suite fails in milliseconds with "executable doesn't exist", which
     * reads like fifty broken tests rather than one missing binary. */
    ...(process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } }
      : {}),
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  reporter: 'list',
})
