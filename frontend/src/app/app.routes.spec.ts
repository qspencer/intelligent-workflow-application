import { describe, expect, it } from 'vitest';

import { routes } from './app.routes';

describe('app.routes', () => {
  it('declares the four known paths plus a default redirect', () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toEqual(['', 'workflows', 'instances', 'instances/:id', 'cost']);
    expect(routes[0].redirectTo).toBe('instances');
    expect(routes[0].pathMatch).toBe('full');
  });

  it('lazy-loads each non-default route', () => {
    for (const route of routes.slice(1)) {
      expect(typeof route.loadComponent).toBe('function');
    }
  });

  it('lazy-loaded modules resolve to component classes', async () => {
    for (const route of routes.slice(1)) {
      const loader = route.loadComponent;
      if (!loader) throw new Error(`route ${route.path} has no loadComponent`);
      const cls = await loader();
      expect(typeof cls).toBe('function');
      expect(cls.name.endsWith('Component')).toBe(true);
    }
  });
});
