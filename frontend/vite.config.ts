import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The gateway (FastAPI) serves REST under /api/v1 and WebSocket under /ws.
// In functional mode the frontend talks to VITE_BACKEND_URL directly; this proxy
// is only used if you opt into same-origin paths (VITE_API_BASE=/api/v1) for
// CORS-free local dev. Its target follows the same backend variable.
const GATEWAY =
  process.env.VITE_BACKEND_URL ?? process.env.VITE_GATEWAY_URL ?? "http://localhost:8000";
const TARGET = /^https?:\/\//i.test(GATEWAY) ? GATEWAY : `http://${GATEWAY}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: TARGET, changeOrigin: true },
      "/ws": { target: TARGET, ws: true, changeOrigin: true },
    },
  },
});
