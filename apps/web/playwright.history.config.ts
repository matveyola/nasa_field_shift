import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: './e2e/workspace', testMatch: 'assistant-history.spec.ts', workers: 1, timeout: 30000, reporter: 'list',
  outputDir: 'test-results/history',
  use: { baseURL: 'http://127.0.0.1:18123', screenshot: 'only-on-failure' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: { command: 'npm run dev -- --port 18123 --strictPort', url: 'http://127.0.0.1:18123', reuseExistingServer: false },
})
