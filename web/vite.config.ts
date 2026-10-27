import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies the local SimuBench service (simubench serve, port 8765).
const service = process.env.SIMUBENCH_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: service, changeOrigin: false, ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
