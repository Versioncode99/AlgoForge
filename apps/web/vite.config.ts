import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The desktop shell's pure modules live outside this app's root and are
    // tested from here; without this vite refuses to read them.
    fs: { allow: ['..', '../..'] },
  },
  preview: { host: '127.0.0.1', port: 4173 },
  test: {
    environment: 'jsdom',
    // The desktop shell's pure modules run here too. They are plain logic over
    // ids -- which window has which workspace, and what a renderer may ask for
    // -- with no Electron import, and that is exactly the part worth testing
    // rather than mocking. A second runner for two files would be a second
    // command nobody remembers to run.
    include: ['src/**/*.test.ts?(x)', '../desktop/*.test.js'],
    // Unmounts between cases and marks the act environment. Without it a
    // container rendered by one test stays in the document for the rest of the
    // file, and React 19 warns that act() is unsupported.
    setupFiles: ['./src/test-setup.ts'],
    // These tests mount the whole application in jsdom and wait for content.
    // Vitest's 5s default is a budget for how fast the machine is, not an
    // assertion any of them make -- and on a slower one the app-wide mounts
    // land either side of it, so the suite failed by stopwatch rather than by
    // behaviour. The assertions are unchanged; only the patience is.
    testTimeout: 20_000,
  },
})
