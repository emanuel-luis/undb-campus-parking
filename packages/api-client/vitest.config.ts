import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['test/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      exclude: ['src/schema.ts', 'src/index.ts'],
      thresholds: { lines: 85, statements: 85, functions: 85, branches: 85 },
    },
  },
})
