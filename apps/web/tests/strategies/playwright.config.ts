import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: '.', testMatch: '**/*.spec.ts', workers: 1, timeout: 30000,
  outputDir: '../../test-results/strategies', reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:18096', screenshot: 'only-on-failure' },
  projects: [{ name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } }, { name: 'mobile', use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } }],
  webServer: { command: 'node node_modules/vite/bin/vite.js --config tests/strategies/vite.config.ts', cwd: new URL('../../', import.meta.url).pathname,
    url: 'http://127.0.0.1:18096/tests/strategies/index.html', reuseExistingServer: false },
})
