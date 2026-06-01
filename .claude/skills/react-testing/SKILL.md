---
name: react-testing
description: >-
  Component + hook testing for THIS frontend — Vitest 2.x + @testing-library/react + jsdom
  + user-event, `globals: false`, react-router-dom. Behavior-focused, accessible queries,
  axe checks, and the RTL-vs-Playwright boundary. Use when writing or fixing .test.tsx
  files, or deciding what to mock.
---

# React testing (Vitest + RTL edition)

Adapted from `affaan-m/ECC` (MIT). Reworked for our exact stack: **Vitest 2.x**, jsdom,
`@testing-library/react` 16 + `user-event` 14 + `jest-dom`, config in `vite.config.ts`
(`environment: 'jsdom'`, `setupFiles: ['src/test-setup.ts']`, `globals: false`). CI runs
`npm test` (`vitest run`) + `npm run build`. No coverage gate today.

## Core principle

Test what the user sees and does — not implementation details. Render with the providers
it has in production, interact via accessible queries + `userEvent`, assert visible output
and observable side effects (callback fired, request sent). Do **not** inspect state/props,
mock React, or assert render counts.

## `globals: false` — import everything

Our config does not inject globals, so **import the test API explicitly** (our existing
specs do this):
```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
```

## Query priority (top-down)

1. **Accessible to everyone:** `getByRole`, `getByLabelText`, `getByPlaceholderText`, `getByText`.
2. **Semantic:** `getByAltText`, `getByTitle`.
3. **Test IDs (escape hatch):** `getByTestId`.

Variants: `getBy*` throws on miss · `queryBy*` returns `null` (use for "assert absence") ·
`findBy*` is async (use for elements that appear after async work).

> **Scope ambiguous names with `within` (real lesson).** When the same accessible name
> exists twice — e.g. a header **Create** button and a dialog **Create** button once the
> dialog opens — `getByRole('button', {name:'Create'})` throws. Scope to the container:
> ```ts
> const dialog = within(screen.getByText('Create automation').closest('.dialog') as HTMLElement);
> fireEvent.click(dialog.getByRole('button', { name: 'Create' }));
> ```

## Interaction

Prefer `userEvent` (a real browser sequence) over `fireEvent` (one synthetic event). Some
of our older specs use `fireEvent`; new tests should use `userEvent`:
```ts
const user = userEvent.setup();        // once per test, reuse `user`
await user.type(screen.getByLabelText('Name'), 'Invoice triage');
await user.click(screen.getByRole('button', { name: /create/i }));
```
Always `await` userEvent calls.

## Async — never `setTimeout`

```ts
expect(await screen.findByText('Done')).toBeInTheDocument();   // appears after async
await waitFor(() => expect(spy).toHaveBeenCalled());           // side effect
await waitForElementToBeRemoved(() => screen.queryByText('Loading'));
```

## Mocking the network

**Today** we stub the API surface directly — fine for unit-level component tests:
```ts
vi.spyOn(api, 'listWorkflows').mockResolvedValue([def({})]);
// always restore so spies don't leak across tests:
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
```
**The upgrade** (when you want network-layer fidelity — real `fetch`/ApiService URL
construction exercised): add **MSW**. Mock at the boundary and fail loud on anything
unmocked:
```ts
// src/test-setup.ts additions
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
export const server = setupServer(
  http.get('/api/workflows', () => HttpResponse.json([])),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```
`onUnhandledRequest: 'error'` — a silent pass is worse than a red test. Prefer MSW once a
component's value is in how it talks to `/api`, not just its render.

## Provider wrapping

Our only production provider is the router. Keep a thin helper rather than re-wrapping in
every file:
```tsx
// test-utils.tsx
export function renderWithRouter(ui: React.ReactElement, { route = '/' } = {}) {
  return render(<MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>);
}
export * from '@testing-library/react';
```
(If we later add a context provider — theme, a query client — add it here, and instantiate
shared fixtures like a `QueryClient` **once per test outside the wrapper closure**, or
cache state resets on every render and tests flake.)

## Hooks

```ts
import { renderHook, act, waitFor } from '@testing-library/react';
const { result } = renderHook(() => useEvents(id));
act(() => result.current.refresh());
await waitFor(() => expect(result.current.items.length).toBe(1));
```
Wrap state-changing calls in `act`; test through the hook's public API; pass a `wrapper`
for hooks that need context. (`renderHook` is one option; our `hooks/useEvents.test.tsx`
takes the other valid route — driving the hook through a small `Harness` component +
`render` — which is preferable when you want to assert the hook's effect on real output.)

## Accessibility assertions

Use **`vitest-axe`** (not jest-axe — we're on Vitest):
```ts
import { axe } from 'vitest-axe';
const { container } = renderWithRouter(<AutomationsHome />);
expect(await axe(container)).toHaveNoViolations();
```
Catches missing labels, invalid ARIA, heading-order, missing alt. **jsdom has no CSS
engine**, so visual contrast is NOT covered here — that belongs in Playwright. Directly
serves the C8 a11y cut.

## RTL vs Playwright boundary

jsdom can't do real layout, animation/transitions, scrolling, drag-and-drop, iframes,
downloads, or true cross-origin. For those, use Playwright. Rule of thumb:
- A hook, a presentational component, a form with logic → **RTL**.
- Layout-dependent behavior or a browser API jsdom lacks → **Playwright**.
- A full multi-page user flow → **Playwright E2E**.

We already run Playwright server-side for the browser *connector* + RPA test; the
`e2e-testing` borrow (POM, auto-wait-over-`waitForTimeout`) applies to both that and any
future frontend E2E.

## Anti-patterns

- `container.querySelector(...)` — bypasses accessible queries; tests pass where real users fail.
- Asserting render counts — implementation detail.
- `vi.mock('react', …)` — never mock React; refactor the component.
- Mocking child components by default — that tests integration away. Mock only heavy-side-effect children.
- Ignoring `act()` warnings — they flag real bugs (state update after unmount, missing async wrap).
- Shared mutable state across tests — order-dependent flakes. `cleanup()` + `vi.restoreAllMocks()` in `afterEach`.
- Snapshots of rendered DOM — break on styling, get rubber-stamped. Snapshot only pure serializers.

## Coverage

We have no coverage gate, and that's fine — treat coverage as guidance, not a target to
game. Prioritize: pure utilities and hooks (high), container components (golden path +
error states), pages (a smoke test; full flows go to Playwright). If we ever add a
threshold, set it in `vite.config.ts` `test.coverage` with the `v8` provider.
