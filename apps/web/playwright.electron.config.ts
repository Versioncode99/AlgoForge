import { defineConfig } from '@playwright/test'

/* Real Electron, deliberately kept out of the browser suite.
 *
 * Doc 2 asks for multi-window lifecycle coverage and says not to substitute a
 * mocked browser test for it. These launch an actual Electron process: a real
 * main process, real BrowserWindows, the real preload bridge and the real IPC
 * contract. Nothing in this file stubs any of that.
 *
 * It is a separate config because the browser suite runs against a Vite server
 * on a port, and these run a binary with no server at all — one `webServer` and
 * one `baseURL` cannot describe both without lying about one of them.
 *
 * Serial by necessity: every test here opens OS windows and the assertions are
 * about how many exist.
 */
export default defineConfig({
  testDir: './tests/electron',
  // Launching a shell, loading a renderer and closing it cleanly is slower than
  // a page navigation, and a tight timeout here reads as a flaky suite rather
  // than as a slow container.
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
})
