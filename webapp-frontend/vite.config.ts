// Minimal typing so tsc doesn't require @types/node for this config file.
declare const process: { env: Record<string, string | undefined> };

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite config for the Telegram Poker mini-app front-end.
// - Dev server proxies /api → backend (default http://localhost:8080)
// - Preview server mirrors the same proxy so you can test prod builds locally
// - Base is './' so the app works when hosted under Telegram or any subpath
// - Keeps the app size/layout intact (no special HTML injection here)
//
// Usage:
//   # backend
//   cd webapp-backend && uvicorn webapp_api:app --reload --port 8080
//
//   # frontend (same repo)
//   cd webapp-frontend && VITE_API_TARGET=http://localhost:8080 npm run dev
//
// If VITE_API_TARGET is not provided, it defaults to http://localhost:8080.

export default defineConfig(({ mode }) => {
  const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8080';

  return {
    plugins: [react()],
    base: './', // important for Telegram mini-app hosting and subpath deployments

    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': {
          target: API_TARGET,
          changeOrigin: true,
          secure: false,
        },
      },
    },

    preview: {
      port: 4173,
      strictPort: true,
      proxy: {
        '/api': {
          target: API_TARGET,
          changeOrigin: true,
          secure: false,
        },
      },
    },

    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
    },

    // Some libs look for process.env in the browser; this keeps them calm.
    define: {
      'process.env': {},
    },
  };
});
