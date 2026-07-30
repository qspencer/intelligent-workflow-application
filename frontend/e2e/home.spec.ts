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
//
// CI's e2e backend runs with triggers (and therefore bundled-example
// seeding) disabled, so these specs import their own fixtures: a definition
// whose id matches a real template id is bundled by construction, and a
// mutating tool in a step drives the effect warning.

const DEV_HEADERS = { 'X-Dev-User': 'e2e', 'X-Dev-Groups': 'admins' };
// Fixture setup/teardown talks to the backend directly — the Vite dev-server
// proxy has shown flaky hangs on bodyless DELETEs, and fixtures don't need
// to exercise the proxy path anyway.
const BACKEND = 'http://localhost:8001';

async function importFixture(
  page: import('@playwright/test').Page,
  id: string,
  tools?: string[],
): Promise<void> {
  const definition = {
    id,
    name: `IA fixture ${id}`,
    trigger: { type: 'manual', config: {} },
    steps: [
      {
        id: 's1',
        name: 's1',
        type: 'agentic',
        goal: 'noop',
        model: 'claude-haiku-4-5',
        ...(tools ? { tools } : {}),
      },
    ],
    edges: [],
  };
  const res = await page.request.post(`${BACKEND}/api/workflows/import`, {
    headers: { ...DEV_HEADERS, 'Content-Type': 'application/json' },
    data: definition,
  });
  if (!res.ok()) throw new Error(`import ${id} failed: ${res.status()}`);
}

async function deleteFixture(page: import('@playwright/test').Page, id: string): Promise<void> {
  // Best-effort teardown: Playwright's request context has shown a
  // reproducible hang on this DELETE (curl against the same endpoint is
  // instant; cause unidentified). Cleanup must never fail the spec — CI
  // backends are ephemeral and the local backend re-seeds on restart.
  await page.request
    .delete(`${BACKEND}/api/workflows/${id}`, { headers: DEV_HEADERS, timeout: 3000 })
    .catch(() => {});
}

test('home shows bundled workflows with a badge; both renderings share the id set', async ({
  page,
}) => {
  // A definition with a TEMPLATE id is bundled by construction.
  const templates = (await (
    await page.request.get(`${BACKEND}/api/templates`, { headers: DEV_HEADERS })
  ).json()) as { id: string }[];
  const bundledId = templates[0].id;
  await importFixture(page, bundledId);
  try {
    await page.goto('/');
    await expect(page.getByText('Bundled').first()).toBeVisible();
    const cardNames = await page.locator('.wf-card-name').allTextContents();

    await page.getByRole('button', { name: 'Table' }).click();
    await expect(page).toHaveURL(/view=table/);
    await expect(page.getByRole('table')).toBeVisible();
    const rowNames = await page.locator('tbody .name-cell').allTextContents();
    expect(new Set(rowNames)).toEqual(new Set(cardNames));
    await expectNoSeriousA11y(page);
  } finally {
    await deleteFixture(page, bundledId);
  }
});

test('/workflows redirects into table mode; /instances redirects to /runs', async ({ page }) => {
  await page.goto('/workflows');
  await expect(page.getByRole('heading', { name: 'Your automations' })).toBeVisible();
  await expect(page).toHaveURL(/\/\?view=table/);

  await page.goto('/instances');
  await expect(page).toHaveURL(/\/runs/);
  await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
});

test('running a mutating workflow from the home requires explicit confirmation', async ({
  page,
}) => {
  const id = `e2e-mutating-${Date.now()}`;
  await importFixture(page, id, ['email_send']);
  try {
    await page.goto('/');
    const card = page.locator('.wf-card', { hasText: `IA fixture ${id}` }).first();
    await card.getByRole('button', { name: /^Run/ }).click();
    await expect(page.getByText(/acts on external systems/)).toBeVisible();
    const runButton = page.getByRole('button', { name: 'Run', exact: true });
    await expect(runButton).toBeDisabled();
    await page.getByLabel(/changes external systems/).check();
    await expect(runButton).toBeEnabled();
    await page.getByRole('button', { name: 'Cancel' }).click();
  } finally {
    await deleteFixture(page, id);
  }
});
