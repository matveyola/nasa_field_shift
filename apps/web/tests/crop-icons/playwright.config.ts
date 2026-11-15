import { defineConfig } from '@playwright/test'
const port = Number(process.env.CROP_ICONS_TEST_PORT ??18138)
export default defineConfig({testDir:'.',testMatch:'*.spec.ts',workers:1,timeout:30000,reporter:'list',
  use:{baseURL:`http://127.0.0.1:${port}`,screenshot:'only-on-failure',trace:'retain-on-failure'},
  projects:[{name:'desktop',use:{viewport:{width:1100,height:1000}}},{name:'mobile',use:{viewport:{width:320,height:900},deviceScaleFactor:3,isMobile:true,hasTouch:true}}],
  webServer:{command:`node node_modules/vite/bin/vite.js --config tests/crop-icons/vite.config.ts --port ${port}`,cwd:new URL('../../',import.meta.url).pathname,url:`http://127.0.0.1:${port}/tests/crop-icons/index.html`,reuseExistingServer:false},
})
