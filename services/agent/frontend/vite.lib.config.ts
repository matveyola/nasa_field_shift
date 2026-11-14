import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Library build of the widget alone (React is the host's): npm run build:lib → dist-lib/
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist-lib",
    emptyOutDir: true,
    sourcemap: true,
    lib: { entry: "src/widget/index.ts", formats: ["es"], fileName: "field-shift-widget", cssFileName: "field-shift-widget" },
    rolldownOptions: { external: ["react", "react-dom", "react/jsx-runtime", "react-dom/client"] },
  },
});
