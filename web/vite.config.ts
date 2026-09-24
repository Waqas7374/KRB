import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
// vitest/config re-exports vite's defineConfig with the `test` key typed.
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // Inside Docker on a Windows/macOS host, file changes on the bind mount
    // do not produce filesystem events in the container, so Vite silently
    // keeps serving stale modules. docker-compose sets VITE_WATCH_POLLING=1
    // for the web service; a native `npm run dev` keeps event-based watching.
    watch: process.env.VITE_WATCH_POLLING === "1" ? { usePolling: true, interval: 300 } : undefined,
  },
  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // Split the heavy, rarely-changing libraries so an app deploy does not
        // invalidate the whole bundle in users' caches.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
          charts: ["recharts"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
