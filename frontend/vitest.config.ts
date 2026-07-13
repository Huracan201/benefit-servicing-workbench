import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["**/*.{test,spec}.{ts,tsx}"],
    // Playwright owns the e2e specs (its own runner + testDir); keep them out of vitest's
    // collection so `npm run test` (vitest) doesn't try to execute @playwright/test specs.
    exclude: ["node_modules", ".next", "e2e/**"],
  },
});
