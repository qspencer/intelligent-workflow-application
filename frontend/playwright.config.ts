import { defineConfig, devices } from '@playwright/test';

/** E2E + a11y harness (C8.2 / docs/TESTING.md T4). Real-browser coverage of the
 *  canvas flows jsdom can't do, plus axe accessibility scans.
 *
 *  webServer starts a self-contained backend (in-memory repos, dev auth, no
 *  triggers, replay Bedrock — no AWS, no side effects) and the Vite dev server
 *  that proxies /api to it. Single worker: the in-memory backend is shared
 *  state across specs. */
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command:
        'cd ../backend && AUTH_MODE=dev BEDROCK_MODE=replay WORKFLOW_PLATFORM_START_TRIGGERS=0 ' +
        'uv run uvicorn workflow_platform.main:app --port 8001',
      url: 'http://localhost:8001/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'npm run dev -- --port 4200',
      url: 'http://localhost:4200',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
