import { expect, test } from '@playwright/test';

import { expectNoSeriousA11y, seedAdmin } from './a11y';

// Unique per run so the spec is isolated even against a non-empty backend.
const NAME = `E2E Canvas ${Date.now()}`;

test.beforeEach(async ({ page }) => seedAdmin(page));

test('create → edit → save confirms, canvas is accessible, then delete', async ({ page }) => {
  await page.goto('/');

  // Create a blank workflow (header Create → name → dialog Create).
  await page.getByRole('button', { name: 'Create' }).click();
  await page.getByPlaceholder('e.g. Invoice triage').fill(NAME);
  await page.locator('.dialog').getByRole('button', { name: 'Create' }).click();

  // Lands on the canvas in edit mode.
  await expect(page).toHaveURL(/\/canvas\/.*edit=1/);
  await expect(page.getByText('Editing')).toBeVisible();

  // Canvas accessibility (real layout — jsdom can't check this).
  await expectNoSeriousA11y(page);

  // Make a change so Save enables, then save — the previously-silent path.
  await page.getByRole('button', { name: '+ Function step' }).click();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved ✓')).toBeVisible();

  // Clean up via the Automations-home delete affordance.
  await page.goto('/');
  await page.getByRole('button', { name: `Delete ${NAME}` }).click();
  await page.locator('.dialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText(NAME)).toHaveCount(0);
});
