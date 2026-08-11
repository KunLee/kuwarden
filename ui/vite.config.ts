import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * Vite configuration for the Workbench.
 *
 * The dev server proxies `/api` to the engine rather than enabling CORS on it. CORS would
 * mean the API accepts cross-origin requests in production too, and the Workbench is the
 * surface that holds credentials — it should be same-origin everywhere.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.KUWARDEN_API_URL ?? "http://127.0.0.1:8080",
        changeOrigin: false,
      },
    },
  },
});
