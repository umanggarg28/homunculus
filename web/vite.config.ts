import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev: vite serves at :5173 and proxies /api + /events to FastAPI at :8765.
// Production: `npm run build` outputs to /dist, served by FastAPI directly.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8765",
      "/events": "http://localhost:8765",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
