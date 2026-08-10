import path from "path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    // The Node-native .mjs suite has its own runner, while the performance
    // budget reads a completed Next build and belongs to the build gate.
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["src/test/performance.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@yimatong/shared": path.resolve(__dirname, "../../packages/shared"),
    },
  },
});
