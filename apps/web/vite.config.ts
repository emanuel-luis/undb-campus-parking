import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  // The lazy ECharts chunk (core, bar, line, canvas) is about 520 kB before gzip; the entry is ~300 kB.
  build: { chunkSizeWarningLimit: 600 },
  server: {
    // Same origin in dev as behind nginx: REST and the live WebSocket both go through /api.
    proxy: { '/api': { target: 'http://localhost:8000', ws: true } },
  },
  test: {
    environment: 'jsdom',
    include: ['test/**/*.test.{ts,tsx}'],
    setupFiles: ['./test/setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx'],
      thresholds: { lines: 80, statements: 80 },
    },
  },
})
