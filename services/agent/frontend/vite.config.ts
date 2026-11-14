import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { referenceBff } from "./dev/referenceBff.ts";

// The demo talks to the agent service through this dev proxy, so the browser sees one origin:
// the same shape as a production backend-for-frontend (see README › Security).
const agentTarget = process.env.FS_AGENT_URL ?? "http://127.0.0.1:8000"; // AGENT_AGENT=deterministic
const scriptedTarget = process.env.FS_AGENT_LLM_URL ?? "http://127.0.0.1:8001"; // AGENT_AGENT=scripted-llm
// The host's own route to the geo / simulation services: the contract stub (python -m integration_stub).
const integrationTarget = process.env.FS_INTEGRATION_URL ?? "http://127.0.0.1:8100";

export default defineConfig({
  plugins: [react(), referenceBff()], // /agent-bff: dev-only signing host backend (dev/referenceBff.ts)
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [fileURLToPath(new URL("..", import.meta.url))] }, // contracts/ and fixtures live one level up
    proxy: {
      "/integration": { target: integrationTarget, changeOrigin: true, rewrite: (path) => path.replace(/^\/integration/, "") },
      "/agent-llm": { target: scriptedTarget, changeOrigin: true, rewrite: (path) => path.replace(/^\/agent-llm/, "") },
      "/agent": { target: agentTarget, changeOrigin: true, rewrite: (path) => path.replace(/^\/agent/, "") },
    },
  },
  preview: { port: 4173, strictPort: true },
  build: {
    target: "es2022",
    sourcemap: true,
    // demo pages: the blank page with the assistant (index.html), the phone preview (phone.html) and the style canvas
    rollupOptions: { input: { main: fileURLToPath(new URL("index.html", import.meta.url)), phone: fileURLToPath(new URL("phone.html", import.meta.url)),
                              canvas: fileURLToPath(new URL("canvas.html", import.meta.url)) } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
