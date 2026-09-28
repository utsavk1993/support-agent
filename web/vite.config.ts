/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],

  server: {
    // In development the client runs on its own port with hot reload, and
    // anything under /api is forwarded to the Python server. Same-origin as
    // far as the browser is concerned, so no CORS to configure.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },

  test: {
    // The components render, so they need a DOM. jsdom provides one without
    // starting a browser, which keeps the suite fast enough to run on save.
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    // Tests live beside the thing they test, so there is no separate
    // directory to keep in step with the source.
    include: ["src/**/*.test.{ts,tsx}"],

    // Build the DOM once per worker rather than once per test file.
    //
    // The default pool gives each file a fresh environment, and setting one
    // up costs more than our tests do — vitest was reporting roughly 65% of
    // the run spent constructing jsdom. Reusing it per worker removes most
    // of that.
    //
    // The trade is isolation: files sharing a worker share globals, so a
    // test that scribbles on `window` can affect the next file. Ours do not,
    // and the setup file re-runs per file regardless.
    pool: "vmThreads",

    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/main.tsx", "src/vite-env.d.ts"],
      // text      for the terminal
      // html      a browsable report, kept as a CI artifact
      // json-summary  the numbers, so CI can print them without scraping
      reporter: ["text", "html", "json-summary"],

      // Every line, branch and function. Not a quality guarantee — code can
      // be fully covered and still wrong — but it does mean untested code
      // cannot arrive unnoticed, and it forces a decision about anything
      // that turns out to be unreachable.
      thresholds: {
        lines: 100,
        branches: 100,
        functions: 100,
        statements: 100,
      },
    },
  },

  build: {
    // Build straight into the directory FastAPI already serves, so
    // production stays one process on one origin.
    outDir: "../static",
    emptyOutDir: true,
  },
});
