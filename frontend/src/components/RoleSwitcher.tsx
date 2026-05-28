import type { ChangeEvent } from 'react';

import { KNOWN_GROUPS } from '../lib/auth';

/**
 * Dev-mode role switcher. Writes `wp.user` + `wp.groups` to localStorage so
 * the API client picks them up on subsequent calls. Reloads the page after a
 * change so every view re-fetches with the new identity. Cheap, predictable.
 */
export function RoleSwitcher() {
  const value = displayGroups();

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
          <option value="admins">Admin</option>
          <option value="designers">Workflow Designer</option>
          <option value="operators">Operator</option>
          <option value="viewers">Viewer</option>
          <option value="auditors">Auditor</option>
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
