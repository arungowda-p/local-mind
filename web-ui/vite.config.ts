import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const outDir = path.resolve(__dirname, "../src/local_mind/ui_dist");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://127.0.0.1:8766", changeOrigin: true },
    },
  },
  build: { outDir, emptyOutDir: true, sourcemap: true },
});
