import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, forward API + MCP calls to the gateway running on :8000.
// In Kubernetes the Ingress does this routing instead (same host, different paths).
const devProxy = {
  "/api": "http://127.0.0.1:8000",
  "/mcp": "http://127.0.0.1:8000",
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: devProxy },
  preview: { port: 4173, proxy: devProxy },
});
