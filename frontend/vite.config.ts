import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4200,
    proxy: {
      '/api': { target: 'http://localhost:8001', secure: false },
      '/ws': { target: 'ws://localhost:8001', ws: true, secure: false },
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
