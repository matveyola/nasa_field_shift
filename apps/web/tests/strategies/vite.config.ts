import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  root: fileURLToPath(new URL('../../', import.meta.url)), plugins: [react()],
  optimizeDeps: { entries: ['tests/strategies/index.html', 'tests/strategies/workflows.html'] },
  server: { host: '127.0.0.1', port: 18096, strictPort: true, fs: { allow: [fileURLToPath(new URL('../../../../', import.meta.url))] } },
})
