import { useEffect, useState } from 'react';

import { api } from '../api/client';
import { getMe } from '../lib/me';
import type { Me } from '../types';

/** Header chip showing who the platform thinks you are: the JIT-persisted
 * user + organization from `GET /api/me`. Renders nothing until loaded and
 * degrades silently on error — identity display must never break the shell. */
export function UserChip() {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let ignore = false;
    void getMe().then((data) => {
      if (!ignore && data) setMe(data);
    });
    return () => {
      ignore = true;
    };
  }, []);

  if (!me) return null;
  const name = me.user?.display_name || me.user?.email || me.identity.sub;
  const org = me.organization?.name;
  const roles = me.identity.roles.join(', ');
  async function signOut(): Promise<void> {
    await api.logout().catch(() => undefined);
    window.location.reload();
  }

  return (
    <span className="user-chip" title={`Roles: ${roles || 'none'}`}>
      <span className="user-chip-name">{name}</span>
      {org && <span className="user-chip-org">@ {org}</span>}
      {me.auth_mode === 'local' && (
        <button className="sign-out" onClick={() => void signOut()}>
          Sign out
        </button>
      )}
    </span>
  );
}
