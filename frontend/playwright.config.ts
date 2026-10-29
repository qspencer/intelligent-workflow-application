import { defineConfig, devices } from '@playwright/test';
import { E2E_BACKEND, E2E_BACKEND_PORT, E2E_FRONTEND, E2E_FRONTEND_PORT } from './e2e/ports';

/** E2E + a11y harness (C8.2 / docs/TESTING.md T4). Real-browser coverage of the
 *  canvas flows jsdom can't do, plus axe accessibility scans.
 *
 *  webServer starts a self-contained backend (in-memory repos, dev auth, no
 *  triggers, replay Bedrock — no AWS, no side effects) and the Vite dev server
 *  that proxies /api to it, on ports only the harness uses (`e2e/ports.ts` —
 *  it once shared production's, and reused it). Single worker: the in-memory backend is shared
 *  state across specs. */
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: E2E_FRONTEND,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /auth\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Local-auth flows run against their own backend (AUTH_MODE=local,
      // seeded test users) through a second Vite instance so cookies +
      // the CSRF Origin check behave exactly as in a real deployment.
      name: 'chromium-local-auth',
      testMatch: /auth\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: 'http://localhost:4300' },
    },
  ],
  webServer: [
    {
      command:
        'cd ../backend && AUTH_MODE=dev BEDROCK_MODE=replay WORKFLOW_PLATFORM_START_TRIGGERS=0 ' +
        `uv run uvicorn workflow_platform.main:app --port ${E2E_BACKEND_PORT}`,
      url: `${E2E_BACKEND}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      // Proxies to the e2e backend, never the default (production's 8001).
      command: `VITE_API_TARGET=${E2E_BACKEND} npm run dev -- --port ${E2E_FRONTEND_PORT}`,
      url: E2E_FRONTEND,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command:
        'cd ../backend && AUTH_MODE=local WORKFLOW_PLATFORM_SEED_TEST_USERS=1 ' +
        'BEDROCK_MODE=replay WORKFLOW_PLATFORM_START_TRIGGERS=0 ' +
        'uv run uvicorn workflow_platform.main:app --port 8097',
      url: 'http://localhost:8097/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'VITE_API_TARGET=http://localhost:8097 npm run dev -- --port 4300',
      url: 'http://localhost:4300',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
