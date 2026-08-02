import { useCallback, useEffect, useState, type FormEvent } from 'react';

import { api, errorMessage } from '../api/client';
import { fmtShort } from '../lib/format';
import { Skeleton } from './Skeleton';
import type { AuditEntry, Organization, PlatformUser, RawTraceGrant } from '../types';

const REASON_CODES = [
  'incident_investigation',
  'customer_support',
  'compliance_audit',
  'debugging',
] as const;

// The governance trail this page surfaces — grant lifecycle + raw-access.
const RAW_ACTIONS = new Set([
  'raw_grant_requested',
  'raw_grant_granted',
  'raw_grant_revoked',
  'raw_grant_expired',
  'raw_trace_access_attempted',
  'raw_trace_release_decided',
  'raw_trace_system_access_attempted',
  'raw_trace_system_access_completed',
]);

const STATE_BADGE: Record<RawTraceGrant['state'], string> = {
  active: 'completed',
  pending: 'running',
  rejected: 'failed',
  cancelled: 'failed',
  revoked: 'failed',
  expired: 'skipped',
};

/** The trust wedge (docs/TRACE_GOVERNANCE_PLAN.md §2/§3): raw-trace read grants
 *  — request / dual-control approve / revoke — plus the raw-access audit log
 *  that no competitor GUI surfaces. Administrator-only (the API enforces it). */
export function GrantsAdmin() {
  const [grants, setGrants] = useState<RawTraceGrant[] | null>(null);
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [log, setLog] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    let ignore = false;
    setError(null);
    void Promise.all([
      api.listRawTraceGrants(),
      api.listUsers().catch(() => [] as PlatformUser[]),
      api.listOrganizations().catch(() => [] as Organization[]),
      api.listRecentAudit(200).catch(() => [] as AuditEntry[]),
    ])
      .then(([g, u, o, a]) => {
        if (ignore) return;
        setGrants(g);
        setUsers(u);
        setOrgs(o);
        setLog(a.filter((e) => RAW_ACTIONS.has(e.action)));
      })
      .catch((err) => !ignore && setError(errorMessage(err, 'Failed to load grants')));
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(reload, [reload]);

  const userLabel = (id: string): string => {
    const u = users.find((x) => x.id === id);
    return u ? (u.email ?? u.sub) : id;
  };

  async function act(fn: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await fn();
      reload();
    } catch (err) {
      setError(errorMessage(err, 'Action failed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-grants">
      <div className="header-row">
        <h2>Raw-trace grants</h2>
      </div>
      <p className="muted">
        Reading raw trace content (mail bodies, tool input/result, recalled history) requires an
        explicit grant — default-off even for Administrators. Platform-wide grants (and, under
        dual-control, every grant) need a second distinct Administrator to approve.
      </p>
      {error && <p className="error">{error}</p>}

      <RequestGrantForm users={users} orgs={orgs} busy={busy} onSubmit={(p) => act(() => api.requestRawTraceGrant(p))} />

      {grants === null ? (
        <Skeleton variant="table" />
      ) : grants.length === 0 ? (
        <p className="muted">No grants.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Principal</th>
              <th>Scope</th>
              <th>State</th>
              <th>Reason</th>
              <th>Requested by</th>
              <th>Approved by</th>
              <th>Expires</th>
              <th className="actions-col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => (
              <tr key={g.id}>
                <td>{userLabel(g.principal_id)}</td>
                <td>{g.org_id ?? <span className="badge running">Platform-wide</span>}</td>
                <td>
                  <span className={`badge ${STATE_BADGE[g.state]}`}>{g.state}</span>
                </td>
                <td>{g.reason_code}</td>
                <td>{userLabel(g.requested_by)}</td>
                <td>{g.approved_by ? userLabel(g.approved_by) : <span className="muted">—</span>}</td>
                <td>{g.expires_at ? fmtShort(g.expires_at) : <span className="muted">—</span>}</td>
                <td className="actions-col">
                  {g.state === 'pending' && (
                    <button disabled={busy} onClick={() => void act(() => api.approveRawTraceGrant(g.id))}>
                      Approve
                    </button>
                  )}
                  {(g.state === 'active' || g.state === 'pending') && (
                    <button disabled={busy} onClick={() => void act(() => api.revokeRawTraceGrant(g.id))}>
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Raw-access log</h3>
      <p className="muted">
        Every raw-trace release + grant change, recorded — the audit no competitor GUI shows.
      </p>
      {log.length === 0 ? (
        <p className="muted">No raw-access events yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Actor</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {log.map((e) => (
              <tr key={e.id}>
                <td>{fmtShort(e.timestamp)}</td>
                <td>
                  <code>{e.action}</code>
                </td>
                <td>{e.actor_id}</td>
                <td className="muted">{summarize(e.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function summarize(detail: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const k of ['surface', 'outcome', 'purpose', 'scope', 'reason_code', 'state', 'reason']) {
    if (detail[k] != null) parts.push(`${k}=${String(detail[k])}`);
  }
  return parts.join(' · ') || '—';
}

function RequestGrantForm({
  users,
  orgs,
  busy,
  onSubmit,
}: {
  users: PlatformUser[];
  orgs: Organization[];
  busy: boolean;
  onSubmit: (payload: {
    principal_id: string;
    org_id: string | null;
    reason_code: string;
    expires_at?: string | null;
  }) => void;
}) {
  const [principal, setPrincipal] = useState('');
  const [scope, setScope] = useState<string>(''); // '' = platform-wide
  const [reason, setReason] = useState<string>(REASON_CODES[3]);
  const [expires, setExpires] = useState('');

  const platformWide = scope === '';

  function submit(e: FormEvent): void {
    e.preventDefault();
    if (!principal) return;
    onSubmit({
      principal_id: principal,
      org_id: platformWide ? null : scope,
      reason_code: reason,
      expires_at: expires ? new Date(expires).toISOString() : null,
    });
  }

  return (
    <form className="grant-request" onSubmit={submit}>
      <label>
        Grant to
        <select value={principal} onChange={(e) => setPrincipal(e.target.value)} required>
          <option value="">Select a user…</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.email ?? u.sub} ({u.org_id})
            </option>
          ))}
        </select>
      </label>
      <label>
        Scope
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">Platform-wide (needs two admins)</option>
          {orgs.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Reason
        <select value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASON_CODES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>
      <label>
        Expires {platformWide && <span className="req">(required)</span>}
        <input
          type="datetime-local"
          value={expires}
          onChange={(e) => setExpires(e.target.value)}
          required={platformWide}
        />
      </label>
      <button type="submit" disabled={busy || !principal}>
        Request grant
      </button>
    </form>
  );
}
