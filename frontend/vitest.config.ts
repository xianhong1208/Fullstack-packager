import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Separate from vite.config.ts so the dev-server proxy and build options do not
// leak into test runs — the two configs answer different questions.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Only our own sources; node_modules holds thousands of vendored tests.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
