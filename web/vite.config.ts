import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  server: {
    port: 5173,
    proxy: {
      // Agent-Memory 面板：与 prod nginx 的 /am/ 反代保持一致，同源才能免登
      "/api/v1": { target: "http://127.0.0.1:8180", changeOrigin: true },
      "/am": { target: "http://127.0.0.1:8180", changeOrigin: true },
      "/assets": { target: "http://127.0.0.1:8180", changeOrigin: true },
      "/logo.png": { target: "http://127.0.0.1:8180", changeOrigin: true },
      // 容器里的 Vega API（web nginx 反代），与 8180 共用同一个 PG
      "/api": { target: "http://127.0.0.1:8180", changeOrigin: true },
      "/webhooks": { target: "http://127.0.0.1:8180", changeOrigin: true },
    },
  },
});
