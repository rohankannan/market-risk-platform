/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    alias: {
      // jsdom has no canvas; page tests assert on the serialized chart option
      "echarts-for-react": fileURLToPath(new URL("./src/test/echartsStub.tsx", import.meta.url)),
    },
  },
});
