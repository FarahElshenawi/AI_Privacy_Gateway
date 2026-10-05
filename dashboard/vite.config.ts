import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy /api/* to the cloud backend.
    // For local dev, the backend runs on localhost:8000.
    // For customer deployments, point VITE_CLOUD_BACKEND_URL at the
    // dashboard's runtime env var instead (see src/api/client.ts).
    proxy: {
      "/api": {
        target: process.env.VITE_CLOUD_BACKEND_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
