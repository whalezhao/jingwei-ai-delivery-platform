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
      // 容器里的 Vega API（web nginx 反代），与 8180 共用同一个 PG
      "/api": { target: "http://127.0.0.1:8180", changeOrigin: true },
      "/webhooks": { target: "http://127.0.0.1:8180", changeOrigin: true },
    },
  },
});
