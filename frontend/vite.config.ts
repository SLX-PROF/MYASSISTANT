import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app is served by FastAPI from app/web (no Node.js on the server).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../app/web",
    emptyOutDir: true,
    assetsInlineLimit: 0, // keep CSP strict: no data: fonts/scripts
    target: "es2022",
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
