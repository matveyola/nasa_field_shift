import { defineConfig, devices } from '../../services/agent/frontend/node_modules/@playwright/test/index.mjs'

export default defineConfig({
  testDir: './e2e/planning-v1', testMatch: '**/*.spec.ts', fullyParallel: false, workers: 1,
  timeout: 30000, expect: { timeout: 5000 }, reporter: [['list']],
  outputDir: 'test-results/planning-v1',
  use: { baseURL: 'http://127.0.0.1:18086', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
    { name: 'phone-preview', use: { viewport: { width: 1100, height: 1000 } } },
  ],
  webServer: { command: 'node node_modules/vite/bin/vite.js --config e2e/planning-v1/vite.config.ts',
    url: 'http://127.0.0.1:18086/e2e/planning-v1/index.html', reuseExistingServer: false, timeout: 60000 },
})
