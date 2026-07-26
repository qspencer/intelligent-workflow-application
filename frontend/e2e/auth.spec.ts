import { expect, test } from '@playwright/test';

import { expectNoSeriousA11y } from './a11y';

/** Local-auth flows (docs/AUTH_PLAN.md), driven in a real browser against a
 *  dedicated AUTH_MODE=local backend with seeded per-role test accounts —
 *  cookies, the App-level 401 gate, and role-driven affordances behave as in
 *  a real deployment (this is the surface jsdom cannot exercise).
 *
 *  Runs in the `chromium-local-auth` project only (baseURL :4300 → backend
 *  :8097). Dev headers from localStorage are sent but MUST be inert here.
 */

const PASSWORD = 'test-password';

async function login(page: import('@playwright/test').Page, email: string): Promise<void> {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('unauthenticated visit lands on the login page (401 gate), accessibly', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  // No shell affordances leak around the gate.
  await expect(page.getByRole('heading', { name: 'Your automations' })).toHaveCount(0);
  await expectNoSeriousA11y(page);
});

test('wrong password shows the backend error as an alert; form stays usable', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByLabel('Email').fill('admin@test.local');
  await page.getByLabel('Password').fill('wrong-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid email or password');
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled();
});

test('admin logs in, sees the shell with write affordances, signs out', async ({ page }) => {
  await login(page, 'admin@test.local');
  await expect(page.getByRole('heading', { name: 'Your automations' })).toBeVisible();
  // Real roles drive affordances: Administrator sees create actions.
  await expect(page.getByRole('button', { name: 'Create', exact: true })).toBeVisible();
  // The dev-mode RoleSwitcher must NOT render in local mode.
  await expect(page.locator('.role-switcher')).toHaveCount(0);

  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  // Session is revoked server-side: reloading stays on the login page.
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
});

test('organization viewer sees the shell but no write affordances', async ({ page }) => {
  await login(page, 'org-viewer@test.local');
  await expect(page.getByRole('heading', { name: 'Your automations' })).toBeVisible();
  // hasRole reads the SESSION's roles here — a viewer gets no create buttons
  // even though localStorage's dev default would claim admin.
  await expect(page.getByRole('button', { name: 'Create', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Describe it' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Browse templates' })).toBeVisible();
});
