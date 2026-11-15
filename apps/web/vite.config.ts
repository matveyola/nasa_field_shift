import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// «Польовий помічник»: built from the agent's own widget source, so the app always ships the current widget.
// Its imports resolve to this app's single copy of React (dedupe). Types: src/features/assistant/agent-widget.d.ts.
const widget = fileURLToPath(new URL('../../services/agent/frontend/src/widget', import.meta.url))

export default defineConfig({
  plugins: [react()],
  cacheDir: 'node_modules/.vite-field-shift',
  optimizeDeps: { include: ['leaflet'] },
  resolve: {
    alias: { '@field-shift/agent-widget': widget + '/index.ts' },
    dedupe: ['react', 'react-dom', 'ajv-formats'],
  },
  server: {
    fs: { allow: ['.', widget] },
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
})
