# Workflow Platform Dashboard

Static Angular 18 dashboard. Per `docs/BUILD_PLAN.md` Week 6: workflow + instance lists, instance detail with step trace, retry / pause / kill / resume buttons, audit log inline, polling-based refresh. Generative UI is deferred to Phase 2+.

## Quick start

```bash
cd frontend
npm install                 # downloads Angular + tooling
npm start                   # ng serve on :4200, proxies /api and /ws to :8000
```

In another terminal, start the backend:

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://workflow:workflow@localhost:5432/workflow \
  AUTH_MODE=dev \
  uv run uvicorn workflow_platform.main:app --reload --port 8000
```

The dashboard expects `AUTH_MODE=dev`; identity is taken from `localStorage`:

```js
localStorage.setItem('wp.user', 'alice');
localStorage.setItem('wp.groups', 'admins');
```

For OIDC (production), replace `auth.interceptor.ts` with one that attaches a Bearer token from the IdP — out of scope for Week 6.

## Layout

```
src/
├── app/
│   ├── app.component.ts          App shell with nav
│   ├── app.config.ts             Providers (router, http, auth interceptor)
│   ├── app.routes.ts             Lazy-loaded routes
│   ├── components/
│   │   ├── workflows-list/       /workflows
│   │   ├── instances-list/       /instances (and ?workflow_id=...)
│   │   └── instance-detail/      /instances/:id (steps + audit + actions)
│   ├── services/
│   │   ├── api.service.ts        Wrapper over /api
│   │   └── auth.interceptor.ts   Dev-mode header injection
│   └── types.ts                  TS mirrors of backend Pydantic models
├── index.html
├── main.ts
└── styles.scss
```

## What's intentionally not here

- **WebSocket integration in the UI** — backend exposes `/ws/events`; the UI currently uses polling (every 3-5s). Wire `WebSocket` directly when the polling cadence becomes a UX issue.
- **Real auth flow** — dev-mode header injection is enough until Phase 2's IdP integration work.
- **Forms / workflow authoring** — Week 6 is read + lifecycle ops only. Authoring lands when we wire up natural-language workflow creation.
- **Generative UI components** — deferred (BUILD_PLAN principle 4).

## Tests

`npm test` is configured but no specs exist yet — Week 6 ships the structure; component specs land alongside component refactors as they harden.
