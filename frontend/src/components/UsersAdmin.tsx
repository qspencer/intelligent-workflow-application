import { useCallback, useEffect, useState, type FormEvent } from 'react';

import { api, errorMessage } from '../api/client';
import { fmtShort } from '../lib/format';
import { getMe } from '../lib/me';
import { Skeleton } from './Skeleton';
import type { Organization, PlatformUser } from '../types';

const ALL_ROLES = [
  'Administrator',
  'Organization Administrator',
  'Organization User',
  'Organization Viewer',
];

interface EditState {
  user: PlatformUser;
  roles: string[];
  password: string;
  orgId: string;
}

/** Admin page over GET/POST/PATCH /api/users (docs/AUTH_PLAN.md §6).
 * Local-issuer rows are editable; SSO rows are display-only (the IdP owns
 * their roles — D4). */
export function UsersAdmin() {
  const [users, setUsers] = useState<PlatformUser[] | null>(null);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [managingOrgs, setManagingOrgs] = useState(false);

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
    api
      .listOrganizations()
      .then((data) => {
        if (!ignore) setOrgs(data);
      })
      .catch(() => {
        /* non-admin scopes may 403 — org affordances just stay hidden */
      });
    void getMe().then((me) => {
      if (!ignore && me) setIsAdmin(me.identity.roles.includes('Administrator'));
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
        <div className="header-actions">
          {isAdmin && (
            <button onClick={() => setManagingOrgs(true)}>Organizations</button>
          )}
          <button onClick={() => setCreating(true)}>Add user</button>
        </div>
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
              <th>Org</th>
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
                <td>{orgs.find((o) => o.id === u.org_id)?.name ?? u.org_id}</td>
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
                        onClick={() =>
                          setEditing({
                            user: u,
                            roles: [...u.roles],
                            password: '',
                            orgId: u.org_id,
                          })
                        }
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
          orgs={isAdmin ? orgs : []}
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
          orgs={isAdmin ? orgs : []}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
      {managingOrgs && (
        <OrganizationsDialog
          orgs={orgs}
          onClose={() => setManagingOrgs(false)}
          onChanged={reload}
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

function OrgSelect({
  orgs,
  value,
  onChange,
}: {
  orgs: Organization[];
  value: string;
  onChange: (org: string) => void;
}) {
  if (orgs.length < 2) return null; // single-org or non-Administrator: nothing to pick
  return (
    <>
      <label htmlFor="org-select">Organization</label>
      <select id="org-select" value={value} onChange={(e) => onChange(e.target.value)}>
        {orgs.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </>
  );
}

function CreateUserDialog({
  orgs,
  onClose,
  onCreated,
}: {
  orgs: Organization[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [roles, setRoles] = useState<string[]>(['Organization Viewer']);
  const [orgId, setOrgId] = useState(orgs[0]?.id ?? 'default');
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.createUser({
        email,
        password,
        roles,
        display_name: displayName || undefined,
        ...(orgs.length > 1 ? { org_id: orgId } : {}),
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
        <OrgSelect orgs={orgs} value={orgId} onChange={setOrgId} />
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
  orgs,
  onChange,
  onClose,
  onSaved,
}: {
  state: EditState;
  orgs: Organization[];
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
        ...(state.orgId !== state.user.org_id ? { org_id: state.orgId } : {}),
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
        <OrgSelect
          orgs={orgs}
          value={state.orgId}
          onChange={(orgId) => onChange({ ...state, orgId })}
        />
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


function OrganizationsDialog({
  orgs,
  onClose,
  onChanged,
}: {
  orgs: Organization[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [newName, setNewName] = useState('');
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.createOrganization(newName);
      setNewName('');
      onChanged();
    } catch (err) {
      setError(errorMessage(err, 'Create failed'));
    }
  }

  async function rename(id: string): Promise<void> {
    const name = renames[id]?.trim();
    if (!name) return;
    try {
      await api.renameOrganization(id, name);
      setRenames((r) => ({ ...r, [id]: '' }));
      onChanged();
    } catch (err) {
      setError(errorMessage(err, 'Rename failed'));
    }
  }

  return (
    <div className="dialog-overlay" role="dialog" aria-modal="true" aria-label="Organizations">
      <div className="dialog">
        <h3>Organizations</h3>
        <p className="muted">
          Organizations are tenant boundaries: users, workflows, runs, audit, and cost are
          scoped to one. Deleting an organization is deliberately unsupported.
        </p>
        <table>
          <thead>
            <tr>
              <th>Id</th>
              <th>Name</th>
              <th>Rename</th>
            </tr>
          </thead>
          <tbody>
            {orgs.map((o) => (
              <tr key={o.id}>
                <td>
                  <code>{o.id}</code>
                </td>
                <td>{o.name}</td>
                <td className="actions-col">
                  <input
                    aria-label={`New name for ${o.id}`}
                    value={renames[o.id] ?? ''}
                    onChange={(e) => setRenames((r) => ({ ...r, [o.id]: e.target.value }))}
                  />
                  <button onClick={() => void rename(o.id)}>Rename</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <form onSubmit={create} className="org-create">
          <label htmlFor="new-org-name">New organization</label>
          <input
            id="new-org-name"
            required
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button type="submit">Create</button>
        </form>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
