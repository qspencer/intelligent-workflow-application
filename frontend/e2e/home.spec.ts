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
