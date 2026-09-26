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

  build: {
    // Build straight into the directory FastAPI already serves, so
    // production stays one process on one origin.
    outDir: "../static",
    emptyOutDir: true,
  },
});
