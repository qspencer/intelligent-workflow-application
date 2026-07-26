import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4200,
    proxy: {
      // Overridable so the e2e harness can point a second dev-server instance
      // at the local-auth backend (playwright.config.ts).
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8001',
        secure: false,
      },
      '/ws': {
        target: (process.env.VITE_API_TARGET ?? 'http://localhost:8001').replace('http', 'ws'),
        ws: true,
        secure: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test-setup.ts'],
    globals: false,
    css: false,
  },
});
