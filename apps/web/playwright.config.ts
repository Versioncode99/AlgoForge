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
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
  ],
  reporter: 'list',
})
