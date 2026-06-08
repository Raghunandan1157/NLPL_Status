import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev/build config. The backend (Flask) runs separately on :5055 and
// is reached directly via VITE_EOD_API_BASE (see src/lib/apiClient.js).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    strictPort: true,
    open: false, // the dev launcher (scripts/dev.mjs) opens the browser once
    watch: {
      // The backend keeps data files (incl. a locked DuckDB) inside the project.
      // Watching them crashes Vite with EBUSY, so ignore everything non-source.
      ignored: [
        "**/eod_data/**",
        "**/backend/**",
        "**/dist/**",
        "**/node_modules/**",
        "**/.git/**",
      ],
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4174,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
