import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    testTimeout: 30_000,
    // CI 下限制 jsdom worker 数：全量并行时重文件（products/[id]、
    // codes/takeover 等）会因负载产生渲染超时 flaky；配合 ci.yml 的
    // --shard 分两片执行，单片文件数与并发都减半。本地开发保持默认
    // 并行度以获得更快反馈。
    ...(process.env.CI ? { maxWorkers: 2, minWorkers: 1 } : {}),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@yimatong/shared": path.resolve(__dirname, "../../packages/shared"),
    },
  },
});
