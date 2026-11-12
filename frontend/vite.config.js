import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to one origin. In development Vite serves the
// pages and forwards API calls to the Flask backend, so no CORS setup is
// needed and the session cookie behaves exactly as it does in production.
const BACKEND = process.env.VITE_BACKEND_URL || "http://127.0.0.1:5000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/health": { target: BACKEND, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Keep every script and stylesheet in external files so the backend's
    // strict Content-Security-Policy (no inline code) holds.
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    css: false,
    restoreMocks: true,
  },
});
