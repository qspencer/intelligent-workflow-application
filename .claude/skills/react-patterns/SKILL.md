---
name: react-patterns
description: >-
  Idiomatic React 18/19 for THIS frontend — a Vite SPA (no Next.js/RSC) migrating from
  Angular toward a React Flow canvas. Hooks discipline, derive-don't-store, state-location
  decision tree, composition, and canvas-scale performance. Use when writing or reviewing
  .tsx components, designing state shape, or porting an Angular view to React.
---

# React patterns (Vite SPA edition)

Adapted from `affaan-m/ECC` (MIT). Reworked for our stack: a **Vite single-page app**
(no Next.js, no Server Components, no server actions), Vitest tests, react-router-dom v6,
a plain `ApiService` over `/api`, and a React Flow (`@xyflow/react`) canvas as the
centerpiece. Where the original leaned on RSC/TanStack, this leans on our reality.

## Core principles

1. **Render is a pure function of props + state.** Derive during render; don't store
   derived values in state via `useEffect`.
   ```tsx
   // Good
   const total = items.reduce((s, i) => s + i.price * i.qty, 0);
   // Bad — extra render, can desync, hides data flow
   const [total, setTotal] = useState(0);
   useEffect(() => setTotal(items.reduce(...)), [items]);
   ```
2. **Side effects live outside render** — in event handlers or `useEffect`, never in the
   render body.
3. **Composition over inheritance** — `children`, render props, component props.

> **Angular→React migration traps** (the ones that actually bite):
> - Reaching for `useEffect` to mirror data the way you'd use an RxJS pipe — usually it's
>   a *derivation*, so compute it in render (principle 1).
> - Expecting Angular's DI/services — there's no service singleton; share via a **hook**
>   or **context**, lift state, or (for the canvas) a store.
> - Expecting zone.js auto-change-detection — React only re-renders on explicit state
>   changes. No "magic" refresh; set state.
> - Two-way `[(ngModel)]` → controlled inputs (`value` + `onChange`).

## Hooks discipline

- Top-level only, never conditional.
- Clean up every subscription / interval / listener (`return () => …`).
- Functional updater when new state depends on old: `setX(prev => prev + 1)`.
- **Default to NOT memoizing.** Add `useMemo`/`useCallback` only when a profiler or a real
  dependency chain proves it matters (memo isn't free — it adds an equality check).
- Extract a custom hook only when the same hook sequence appears in 2+ components.

## State location decision tree

```
Used by one component?                      → useState inside it
Used by parent + a few descendants?         → lift to nearest common ancestor
Cross-tree, low-frequency reads             → React Context
  (theme, auth identity, role, dev-toggle)
High-frequency updates shared across tree?  → external store (Zustand)
Derived from the server?                    → fetch in ApiService; cache in a hook
```

Today our shared bits are small (role/identity in `localStorage`, advanced-toggle) — no
global store yet. **The React Flow canvas is where a store will earn its place:** node/edge
graph state with frequent updates is the textbook Zustand case. When you add one, split it
by concern and read the `click-path-audit` borrow — graph stores are where "button does
nothing / final state contradicts intent" bugs live.

## Data fetching (our reality)

We use a plain `ApiService` over `/api` and component-local `useState` + `useEffect` (often
polling). That's fine at our scale. Two rules keep it honest:

- **Guard against the `useEffect` + fetch race:** track an `ignore`/`AbortController` so a
  late response from a stale render doesn't clobber current state.
  ```tsx
  useEffect(() => {
    let ignore = false;
    api.listInstances().then(r => { if (!ignore) setRows(r); });
    return () => { ignore = true; };
  }, [filter]);
  ```
- If client-side cache + invalidation + retries start getting hand-rolled, that's the
  signal to adopt **TanStack Query** — not before.

## Composition recipes

- **Slot via `children`** and **named slots** (`<Page header={…} sidebar={…}>`).
- **Compound components** (shared state via Context) — the right shape for the canvas
  inspector / tabbed panels:
  ```tsx
  <Tabs defaultValue="config">
    <Tabs.List><Tabs.Trigger value="config">Config</Tabs.Trigger></Tabs.List>
    <Tabs.Panel value="config"><StepConfig/></Tabs.Panel>
  </Tabs>
  ```
- **Render prop / hook** — prefer a `useX()` hook returning the same shape over a
  function-as-child when the parent just needs the data.

## Suspense + error boundaries

```tsx
<ErrorBoundary fallback={<ErrorView/>}>
  <Suspense fallback={<Skeleton/>}><InstanceDetail id={id}/></Suspense>
</ErrorBoundary>
```
- Put Suspense close to the data, not at the route root — reveal progressively.
- Error boundaries are still a class API (use `react-error-boundary` for a hook wrapper).
  They catch render/lifecycle errors — **NOT** errors in event handlers or async callbacks
  (handle those explicitly).

## Forms

We're a Vite SPA, so React 19 **server actions / `useActionState` don't apply** (they need
a server runtime). Our path:
- **Controlled inputs** when the value drives other UI, formats per keystroke, or validates
  live (our Create/Import dialogs).
- For multi-step / dynamic field arrays / cross-field validation, reach for **react-hook-form**
  rather than hand-rolling form state — that's a maintenance trap past trivial complexity.

## Performance (canvas-scale)

This matters most on the React Flow canvas, which can render many nodes:
- **`React.memo` only when** a component re-renders often, its props are usually unchanged,
  *and* its render is measurably expensive. Otherwise it's pure overhead.
- **Split context** — one context per concern so a theme change doesn't re-render role
  consumers.
- **Stable `key`s** = the entity id (workflow/step id), never the array index.
- **Virtualize** lists past ~50 non-trivial rows (`@tanstack/react-virtual`); for the canvas,
  lean on React Flow's own viewport culling and don't define node components inline.
- Deeper tiers (re-render, `content-visibility`, derived booleans) → the ECC
  `react-performance` skill is the reference; cite it during a canvas perf pass.

## Accessibility-first composition

Semantic HTML (`<button>`, `<a>`, `<nav>`, `<main>`) before `role=`; every interactive
element keyboard-reachable; inputs need labels; manage focus on route change + modal
open/close. The how-to + checklist live in `docs/UI_POLISH_AND_A11Y.md`; assert it with
`axe` per the `react-testing` skill.

## Out of scope

Next.js App Router, Server/Client Components, Server Actions, RSC data loading, React
Native — none apply to a Vite SPA. If we ever add SSR, revisit. Router specifics live in
react-router-dom docs; these patterns are router-agnostic.
