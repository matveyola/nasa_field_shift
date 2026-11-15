import { defineConfig, devices } from '@playwright/test'
const port = Number(process.env.ANALYTICS_TEST_PORT ?? 18103)
export default defineConfig({ testDir: '.', testMatch: '*.spec.ts', workers: 1, timeout: 30_000,
  outputDir: '../../test-results/analytics', reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } }],
  webServer: { command: `node node_modules/vite/bin/vite.js --config tests/analytics/vite.config.ts --port ${port}`, cwd: new URL('../../', import.meta.url).pathname,
    url: `http://127.0.0.1:${port}/tests/analytics/index.html`, reuseExistingServer: false, timeout: 60_000 },
})
