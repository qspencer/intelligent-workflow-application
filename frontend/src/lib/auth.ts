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

const GROUP_TO_ROLE: Record<string, string> = {
  admins: 'Administrator',
  'org-admins': 'Organization Administrator',
  'org-users': 'Organization User',
  'org-viewers': 'Organization Viewer',
};

/** Real roles from the authenticated session (local/oidc modes). Set by App
 * once /api/me resolves; null means dev mode (or unknown) — fall back to the
 * localStorage group the RoleSwitcher manages. Without this, local-mode
 * viewers saw every write affordance (hasRole defaulted to admins) and
 * collected 403s on click. */
let sessionRoles: string[] | null = null;

export function setSessionRoles(roles: string[] | null): void {
  sessionRoles = roles;
}

/**
 * Whether the current identity has any of the given group values. When a
 * real session's roles are cached (local/oidc), those decide — group names
 * map to role names. Otherwise dev-mode behavior: an unset localStorage
 * acts as 'admins' (matching the headers sent by `authHeaders`).
 */
export function hasRole(allowed: string[]): boolean {
  const roles = sessionRoles;
  if (roles !== null) {
    return allowed.some((group) => roles.includes(GROUP_TO_ROLE[group] ?? group));
  }
  const stored = localStorage.getItem('wp.groups');
  const effective = stored ?? 'admins';
  return allowed.includes(effective);
}
