import { useEffect, useState, type ChangeEvent } from 'react';

import { KNOWN_GROUPS } from '../lib/auth';
import { getMe } from '../lib/me';

/**
 * Dev-mode role switcher. Writes `wp.user` + `wp.groups` to localStorage so
 * the API client picks them up on subsequent calls. Reloads the page after a
 * change so every view re-fetches with the new identity. Cheap, predictable.
 */
export function RoleSwitcher() {
  const value = displayGroups();
  // Picking your own role only exists in dev mode; in local/oidc mode the
  // backend owns roles (docs/AUTH_PLAN.md §6). Shown until the mode is
  // known to be non-dev, so the dev loop works even with the backend down.
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let ignore = false;
    void getMe().then((me) => {
      if (!ignore && me && me.auth_mode !== 'dev') setHidden(true);
    });
    return () => {
      ignore = true;
    };
  }, []);

  if (hidden) return null;

  function onChange(event: ChangeEvent<HTMLSelectElement>): void {
    const groups = event.target.value;
    localStorage.setItem('wp.groups', groups);
    if (!localStorage.getItem('wp.user')) {
      localStorage.setItem('wp.user', 'dev-user');
    }
    window.location.reload();
  }

  return (
    <div className="role-switcher">
      <label>
        <span className="label">Acting as</span>
        <select value={value} onChange={onChange}>
          <option value="admins">Administrator</option>
          <option value="org-admins">Organization Administrator</option>
          <option value="org-users">Organization User</option>
          <option value="org-viewers">Organization Viewer</option>
        </select>
      </label>
    </div>
  );
}

/** Stored group if it's a known value; otherwise default to admins. */
function displayGroups(): string {
  const stored = localStorage.getItem('wp.groups');
  return stored && (KNOWN_GROUPS as readonly string[]).includes(stored)
    ? stored
    : 'admins';
}
