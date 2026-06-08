import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

/** Run axe and fail on any serious/critical violation. Moderate/minor are
 *  surfaced in the assertion message but don't gate (tightened over time). */
export async function expectNoSeriousA11y(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  );
  // Include the offending selectors/markup in the failure message so a
  // regression points straight at the element.
  const detail = serious.map((v) => ({
    id: v.id,
    impact: v.impact,
    nodes: v.nodes.map((n) => ({ target: n.target, html: n.html })),
  }));
  expect(detail, JSON.stringify(detail, null, 2)).toEqual([]);
}

/** Seed a dev admin identity so role-gated actions (create/delete) work. */
export async function seedAdmin(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('wp.user', 'e2e');
    localStorage.setItem('wp.groups', 'admins');
  });
}
