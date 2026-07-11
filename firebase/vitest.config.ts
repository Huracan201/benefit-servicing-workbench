import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    // Emulator round-trips are slower than pure unit tests.
    testTimeout: 15_000,
    hookTimeout: 15_000,
    // One shared Firestore emulator is stateful; don't run test files in parallel.
    fileParallelism: false,
  },
});
