import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The gateway (FastAPI) serves REST under /api/v1 and WebSocket under /ws.
// In dev we proxy both to the backend so the frontend can use same-origin paths.
// Override the target with VITE_GATEWAY_URL if the gateway runs elsewhere.
const GATEWAY = process.env.VITE_GATEWAY_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: GATEWAY, changeOrigin: true },
      "/ws": { target: GATEWAY, ws: true, changeOrigin: true },
    },
  },
});
