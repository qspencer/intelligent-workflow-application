/**
 * Dev-mode auth: identify the user via X-Dev-User / X-Dev-Groups headers,
 * read from localStorage. In production (AUTH_MODE=oidc on the backend),
 * this is replaced by a Bearer token from the IdP. Out of scope for now.
 */

export const KNOWN_GROUPS = [
  'admins',
  'org-admins',
  'org-users',
  'org-viewers',
] as const;

export function currentUser(): string {
  return localStorage.getItem('wp.user') ?? 'dev-user';
}

export function currentGroups(): string {
  return localStorage.getItem('wp.groups') ?? 'admins';
}

export function authHeaders(): Record<string, string> {
  return {
    'X-Dev-User': currentUser(),
    'X-Dev-Groups': currentGroups(),
  };
}

/**
 * Whether the current dev-mode identity has any of the given group values.
 * An unset localStorage acts as 'admins' in dev mode (matching the headers
 * sent by `authHeaders`). In production OIDC mode there's no localStorage,
 * so this returns false and any role hint stays visible.
 */
export function hasRole(allowed: string[]): boolean {
  const stored = localStorage.getItem('wp.groups');
  const effective = stored ?? 'admins';
  return allowed.includes(effective);
}
