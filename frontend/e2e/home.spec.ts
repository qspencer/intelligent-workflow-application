import { expect, test } from '@playwright/test';

import { expectNoSeriousA11y, seedAdmin } from './a11y';

test.beforeEach(async ({ page }) => seedAdmin(page));

test('Automations home loads and is accessible', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your automations' })).toBeVisible();
  await expectNoSeriousA11y(page);
});

test('Templates gallery loads and is accessible', async ({ page }) => {
  await page.goto('/templates');
  await expect(page.getByRole('button', { name: 'Use this template' }).first()).toBeVisible();
  await expectNoSeriousA11y(page);
});

// --- IA rework (IA_PLAN): merged catalog, redirects, effect warnings ---

test('home shows bundled workflows with a badge; both renderings share the id set', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByText('Bundled').first()).toBeVisible();
  const cardNames = await page.locator('.wf-card-name').allTextContents();

  await page.getByRole('button', { name: 'Table' }).click();
  await expect(page).toHaveURL(/view=table/);
  await expect(page.getByRole('table')).toBeVisible();
  const rowNames = await page.locator('tbody .name-cell').allTextContents();
  expect(new Set(rowNames)).toEqual(new Set(cardNames));
  await expectNoSeriousA11y(page);
});

test('/workflows redirects into table mode; /instances redirects to /runs', async ({ page }) => {
  await page.goto('/workflows');
  await expect(page.getByRole('heading', { name: 'Your automations' })).toBeVisible();
  await expect(page).toHaveURL(/\/\?view=table/);

  await page.goto('/instances');
  await expect(page).toHaveURL(/\/runs/);
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
});

test('running a mutating bundled workflow from the home requires explicit confirmation', async ({
  page,
}) => {
  await page.goto('/');
  // email-triage-apply is bundled + mutating (email_label_apply tool).
  const card = page.locator('.wf-card', { hasText: 'Email Triage (acting' }).first();
  await card.getByRole('button', { name: /^Run/ }).click();
  await expect(page.getByText(/acts on external systems/)).toBeVisible();
  const runButton = page.getByRole('button', { name: 'Run', exact: true });
  await expect(runButton).toBeDisabled();
  await page.getByLabel(/changes external systems/).check();
  await expect(runButton).toBeEnabled();
  // Close without firing — this is a live-backend e2e run.
  await page.getByRole('button', { name: 'Cancel' }).click();
});
