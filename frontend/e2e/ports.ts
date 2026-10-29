/** Ports the e2e harness owns — ONE definition, imported by the Playwright
 *  config and by any spec that talks to the backend directly.
 *
 *  These must never be ports anything else uses. The harness used to start
 *  its backend on 8001 and its Vite server on 4200 — the production backend's
 *  port and the dev frontend's — with `reuseExistingServer` on outside CI. On
 *  a box running the real service, Playwright reused it: the "in-memory, no
 *  side effects" backend was production, and ten `e2e-mutating-*` fixtures
 *  whose teardown failed sat in the production database holding a live
 *  `email_send` until 2026-10-29. */
export const E2E_BACKEND_PORT = 8096;
export const E2E_FRONTEND_PORT = 4201;
export const E2E_BACKEND = `http://localhost:${E2E_BACKEND_PORT}`;
export const E2E_FRONTEND = `http://localhost:${E2E_FRONTEND_PORT}`;
