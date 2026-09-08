import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  preview: { host: '127.0.0.1', port: 4173 },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts?(x)'],
    // These tests mount the whole application in jsdom and wait for content.
    // Vitest's 5s default is a budget for how fast the machine is, not an
    // assertion any of them make -- and on a slower one the app-wide mounts
    // land either side of it, so the suite failed by stopwatch rather than by
    // behaviour. The assertions are unchanged; only the patience is.
    testTimeout: 20_000,
  },
})
