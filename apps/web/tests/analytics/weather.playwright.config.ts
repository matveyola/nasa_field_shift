import original from './playwright.config'
export default {...original,testMatch:'*.spec.ts',outputDir:'../../test-results/analytics-weather',
  use:{...original.use,baseURL:'http://127.0.0.1:18113'},
  webServer:{...original.webServer,command:'node node_modules/vite/bin/vite.js --config tests/analytics/vite.config.ts --port 18113',url:'http://127.0.0.1:18113/tests/analytics/index.html'},
}
