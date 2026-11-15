import { fileURLToPath } from 'node:url'
import { defineConfig, mergeConfig } from 'vite'
import application from '../../vite.config'
export default defineConfig(mergeConfig(application, {
  root: fileURLToPath(new URL('../../', import.meta.url)),
  server: { host: '127.0.0.1', port: 18103, strictPort: true,
    fs: { allow: [fileURLToPath(new URL('../../../../', import.meta.url))] } },
}))
