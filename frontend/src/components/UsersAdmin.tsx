import { useCallback, useEffect, useState, type FormEvent } from 'react';

import { api, errorMessage } from '../api/client';
import { fmtShort } from '../lib/format';
import { Skeleton } from './Skeleton';
import type { PlatformUser } from '../types';

const ALL_ROLES = ['Admin', 'Workflow Designer', 'Operator', 'Viewer', 'Auditor'];

interface EditState {
  user: PlatformUser;
  roles: string[];
  password: string;
}

/** Admin page over GET/POST/PATCH /api/users (docs/AUTH_PLAN.md §6).
 * Local-issuer rows are editable; SSO rows are display-only (the IdP owns
 * their roles — D4). */
export function UsersAdmin() {
  const [users, setUsers] = useState<PlatformUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<EditState | null>(null);

  const reload = useCallback(() => {
    let ignore = false;
    api
      .listUsers()
      .then((data) => {
        if (!ignore) {
          setUsers(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!ignore) setError(errorMessage(err, 'Failed to load users'));
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(reload, [reload]);

  async function toggleActive(user: PlatformUser): Promise<void> {
    try {
      await api.updateUser(user.id, { is_active: !user.is_active });
      reload();
    } catch (err) {
      setError(errorMessage(err, 'Update failed'));
    }
  }

  return (
    <div className="page-users">
      <div className="header-row">
        <h2>Users</h2>
        <button onClick={() => setCreating(true)}>Add user</button>
      </div>
      {error && <p className="error">{error}</p>}
      {users === null ? (
        <Skeleton count={4} />
      ) : (
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Source</th>
              <th>Roles</th>
              <th>Status</th>
              <th>Last seen</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email ?? <span className="muted">—</span>}</td>
                <td>{u.display_name ?? <span className="muted">—</span>}</td>
                <td>
                  <code>{u.iss === 'local' ? 'local' : u.iss}</code>
                </td>
                <td>{u.roles.length > 0 ? u.roles.join(', ') : <span className="muted">—</span>}</td>
                <td>
                  <span className={`badge ${u.is_active ? 'completed' : 'failed'}`}>
                    {u.is_active ? 'active' : 'disabled'}
                  </span>
                </td>
                <td>{fmtShort(u.last_seen_at)}</td>
                <td className="actions-col">
                  {u.iss === 'local' && (
                    <>
                      <button
                        onClick={() => setEditing({ user: u, roles: [...u.roles], password: '' })}
                      >
                        Edit
                      </button>
                      <button onClick={() => void toggleActive(u)}>
                        {u.is_active ? 'Disable' : 'Enable'}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {creating && (
        <CreateUserDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            reload();
          }}
        />
      )}
      {editing && (
        <EditUserDialog
          state={editing}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function RoleChecklist({
  value,
  onChange,
}: {
  value: string[];
  onChange: (roles: string[]) => void;
}) {
  function toggle(role: string): void {
    onChange(value.includes(role) ? value.filter((r) => r !== role) : [...value, role]);
  }
  return (
    <fieldset className="role-checklist">
      <legend>Roles</legend>
      {ALL_ROLES.map((role) => (
        <label key={role}>
          <input type="checkbox" checked={value.includes(role)} onChange={() => toggle(role)} />{' '}
          {role}
        </label>
      ))}
    </fieldset>
  );
}

function CreateUserDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [roles, setRoles] = useState<string[]>(['Viewer']);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.createUser({
        email,
        password,
        roles,
        display_name: displayName || undefined,
      });
      onCreated();
    } catch (err) {
      setError(errorMessage(err, 'Create failed'));
    }
  }

  return (
    <div className="dialog-overlay" role="dialog" aria-modal="true" aria-label="Add user">
      <form className="dialog" onSubmit={onSubmit}>
        <h3>Add user</h3>
        <label htmlFor="nu-email">Email</label>
        <input
          id="nu-email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <label htmlFor="nu-name">Display name</label>
        <input id="nu-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <label htmlFor="nu-password">Password (min 8 characters)</label>
        <input
          id="nu-password"
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <RoleChecklist value={roles} onChange={setRoles} />
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit">Create</button>
        </div>
      </form>
    </div>
  );
}

function EditUserDialog({
  state,
  onChange,
  onClose,
  onSaved,
}: {
  state: EditState;
  onChange: (next: EditState) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.updateUser(state.user.id, {
        roles: state.roles,
        ...(state.password ? { password: state.password } : {}),
      });
      onSaved();
    } catch (err) {
      setError(errorMessage(err, 'Update failed'));
    }
  }

  return (
    <div className="dialog-overlay" role="dialog" aria-modal="true" aria-label="Edit user">
      <form className="dialog" onSubmit={onSubmit}>
        <h3>Edit {state.user.email}</h3>
        <RoleChecklist value={state.roles} onChange={(roles) => onChange({ ...state, roles })} />
        <label htmlFor="eu-password">New password (leave blank to keep)</label>
        <input
          id="eu-password"
          type="password"
          minLength={8}
          value={state.password}
          onChange={(e) => onChange({ ...state, password: e.target.value })}
        />
        <p className="muted">Role or password changes sign the user out everywhere.</p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit">Save</button>
        </div>
      </form>
    </div>
  );
}
