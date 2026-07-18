import { authHeaders } from '../lib/auth';
import type {
  Me,
  Organization,
  PlatformUser,
  AuditEntry,
  BatchRunResult,
  CapabilityReport,
  CostRowByDay,
  CostRowByModel,
  CostEstimate,
  CostRowByWorkflow,
  DevErrorsResponse,
  ExplainStep,
  ScaffoldResult,
  ValidationResult,
  WorkflowCatalog,
  InstanceDetail,
  WorkflowDefinition,
  WorkflowInstance,
  WorkflowTemplate,
} from '../types';

const API_BASE = '/api';

/** Error carrying the backend's `detail` payload when present. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** Best-effort human-readable message from an unknown thrown value. */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail !== undefined && err.detail !== null) {
      return JSON.stringify(err.detail);
    }
    return err.message || fallback;
  }
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}

interface RequestOptions {
  query?: Record<string, unknown>;
  body?: BodyInit;
  headers?: Record<string, string>;
}

/** Drop null/undefined/empty values; arrays become repeated params. */
function buildQuery(input: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  return params.toString();
}

async function request<T>(
  method: string,
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  let url = `${API_BASE}${path}`;
  if (opts.query) {
    const qs = buildQuery(opts.query);
    if (qs) url += `?${qs}`;
  }
  const res = await fetch(url, {
    method,
    headers: { ...authHeaders(), ...(opts.headers ?? {}) },
    body: opts.body,
  });
  if (res.status === 401 && path !== '/auth/login') {
    // Local-mode session expired/absent — App listens and shows the login
    // page. Harmless in dev mode (dev headers rarely 401).
    window.dispatchEvent(new CustomEvent('wp:unauthorized'));
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

async function toApiError(res: Response): Promise<ApiError> {
  let detail: unknown;
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      detail = (body as { detail: unknown }).detail;
    } else {
      detail = body;
    }
    if (typeof detail === 'string') message = detail;
  } catch {
    // Non-JSON error body; keep the status-based message.
  }
  return new ApiError(res.status, message, detail);
}

function postJson<T>(path: string, payload: unknown): Promise<T> {
  return request<T>('POST', path, {
    body: JSON.stringify(payload ?? {}),
    headers: { 'Content-Type': 'application/json' },
  });
}

export const api = {
  listWorkflows(): Promise<WorkflowDefinition[]> {
    return request('GET', '/workflows');
  },

  /** Full definition (steps + edges) for one workflow — drives the canvas. */
  getWorkflow(id: string): Promise<WorkflowDefinition> {
    return request('GET', `/workflows/${id}`);
  },

  /** Hard-delete a workflow definition + its run history (Admin/Designer).
   *  Returns the cascade counts. */
  deleteWorkflow(
    id: string,
  ): Promise<{ deleted_workflow: string; deleted_instances: number; deleted_steps: number }> {
    return request('DELETE', `/workflows/${id}`);
  },

  /** Map of `workflow_id → instance count`, aggregated server-side. */
  workflowInstanceCounts(): Promise<Record<string, number>> {
    return request('GET', '/workflows/instance-counts');
  },

  /** Bundled example workflows offered as starting points (canvas C5.2). */
  listTemplates(): Promise<WorkflowTemplate[]> {
    return request('GET', '/templates');
  },

  /** Create a new workflow — blank, or cloned from a template. Returns the
   *  persisted definition so the caller can open it on the canvas. */
  createWorkflow(spec: { name?: string; template_id?: string } = {}): Promise<WorkflowDefinition> {
    return postJson('/workflows', spec);
  },

  listInstances(
    params: { workflow_id?: string; state?: string; limit?: number } = {},
  ): Promise<WorkflowInstance[]> {
    return request('GET', '/workflow-instances', { query: params });
  },

  getInstance(id: string): Promise<InstanceDetail> {
    return request('GET', `/workflow-instances/${id}`);
  },

  instanceAudit(id: string): Promise<AuditEntry[]> {
    return request('GET', `/workflow-instances/${id}/audit`);
  },

  login(email: string, password: string): Promise<{ ok: boolean }> {
    return postJson('/auth/login', { email, password });
  },

  logout(): Promise<{ ok: boolean }> {
    return postJson('/auth/logout', {});
  },

  listUsers(): Promise<PlatformUser[]> {
    return request('GET', '/users');
  },

  createUser(payload: {
    email: string;
    password: string;
    roles: string[];
    display_name?: string;
  }): Promise<PlatformUser> {
    return postJson('/users', payload);
  },

  listOrganizations(): Promise<Organization[]> {
    return request('GET', '/organizations');
  },

  createOrganization(name: string): Promise<Organization> {
    return postJson('/organizations', { name });
  },

  renameOrganization(id: string, name: string): Promise<Organization> {
    return request('PATCH', `/organizations/${id}`, {
      body: JSON.stringify({ name }),
      headers: { 'Content-Type': 'application/json' },
    });
  },

  updateUser(
    id: string,
    payload: {
      roles?: string[];
      is_active?: boolean;
      display_name?: string;
      password?: string;
      org_id?: string;
    },
  ): Promise<PlatformUser> {
    return request('PATCH', `/users/${id}`, {
      body: JSON.stringify(payload),
      headers: { 'Content-Type': 'application/json' },
    });
  },

  me(): Promise<Me> {
    return request('GET', '/me');
  },

  pauseInstance(id: string): Promise<unknown> {
    return postJson(`/workflow-instances/${id}/pause`, {});
  },

  resumeInstance(id: string): Promise<unknown> {
    return postJson(`/workflow-instances/${id}/resume`, {});
  },

  retryInstance(id: string): Promise<unknown> {
    return postJson(`/workflow-instances/${id}/retry`, {});
  },

  killInstance(id: string): Promise<unknown> {
    return postJson(`/workflow-instances/${id}/kill`, {});
  },

  /** Hard-delete a terminal instance. Backend rejects non-terminal with 400. */
  deleteInstance(id: string): Promise<unknown> {
    return request('DELETE', `/workflow-instances/${id}`);
  },

  /** Bulk hard-delete every instance whose state is in `states`. */
  deleteInstancesByStates(
    states: Array<'completed' | 'failed' | 'killed'>,
    workflow_id?: string,
  ): Promise<{ deleted_instances: number; deleted_steps: number }> {
    const query: Record<string, unknown> = { state: states };
    if (workflow_id) query['workflow_id'] = workflow_id;
    return request('DELETE', '/workflow-instances', { query });
  },

  importWorkflow(
    body: string,
    contentType: 'yaml' | 'json',
  ): Promise<{ status: string; workflow_id: string }> {
    const mime = contentType === 'json' ? 'application/json' : 'application/x-yaml';
    return request('POST', '/workflows/import', {
      body,
      headers: { 'Content-Type': mime },
    });
  },

  runWorkflow(
    workflowId: string,
    triggerPayload: Record<string, unknown>,
  ): Promise<{ status: string; instance_id: string; state: string }> {
    return postJson(`/workflows/${workflowId}/run`, triggerPayload);
  },

  /** Batch run (C8.1): fire one instance per payload row. */
  runBatch(workflowId: string, rows: Record<string, unknown>[]): Promise<BatchRunResult> {
    return postJson(`/workflows/${workflowId}/run-batch`, rows);
  },

  /** Authoring catalog (C7.2): triggers + functions + tools for the picker. */
  getCatalog(): Promise<WorkflowCatalog> {
    return request('GET', '/catalog');
  },

  /** NL scaffold (C7.1): describe a workflow in plain English; the server
   *  drafts + persists it and returns the new id to open on the canvas. */
  scaffoldWorkflow(description: string): Promise<ScaffoldResult> {
    return postJson('/workflows/scaffold', { description });
  },

  /** Build-time validation (C7.3): structural findings for a (possibly unsaved)
   *  definition, keyed to node/edge. */
  validateWorkflow(def: WorkflowDefinition): Promise<ValidationResult> {
    return postJson('/workflows/validate', def);
  },

  /** Dry-run (C6.1): sandboxed test — MockWorld, external tools disabled, live
   *  Bedrock. Returns the persisted (dry-run-tagged) instance id. */
  dryRunWorkflow(
    workflowId: string,
    triggerPayload: Record<string, unknown>,
  ): Promise<{ status: string; instance_id: string; state: string; dry_run: boolean; sandbox: string }> {
    return postJson(`/workflows/${workflowId}/dry-run`, triggerPayload);
  },

  forkInstance(
    id: string,
    fromStepId: string,
  ): Promise<{
    status: string;
    source_instance_id: string;
    instance_id: string;
    state: string;
  }> {
    return postJson(`/workflow-instances/${id}/fork`, { from_step_id: fromStepId });
  },

  costByWorkflow(since?: string): Promise<CostRowByWorkflow[]> {
    return request('GET', '/cost/by-workflow', { query: { since } });
  },

  costByModel(since?: string): Promise<CostRowByModel[]> {
    return request('GET', '/cost/by-model', { query: { since } });
  },

  costByDay(since?: string): Promise<CostRowByDay[]> {
    return request('GET', '/cost/by-day', { query: { since } });
  },

  /** Pre-run cost context for the Run dialog (C6.2). */
  getCostEstimate(id: string): Promise<CostEstimate> {
    return request('GET', `/workflows/${id}/cost-estimate`);
  },

  /** Per-agentic-step tool capability boundary (C6.3). */
  getCapabilities(id: string): Promise<CapabilityReport> {
    return request('GET', `/workflows/${id}/capabilities`);
  },

  /** Explain-this-run forensic view of one step in a run (C6.4). */
  getExplain(instanceId: string, stepId: string): Promise<ExplainStep> {
    return request('GET', `/workflow-instances/${instanceId}/steps/${stepId}/explain`);
  },

  /** Dev-only: recent backend ERROR logs (404s when not running AUTH_MODE=dev). */
  getDevErrors(): Promise<DevErrorsResponse> {
    return request('GET', '/dev/errors');
  },

  /** Dev-only: clear the captured-error buffer. */
  clearDevErrors(): Promise<{ status: string }> {
    return postJson('/dev/errors/clear', {});
  },
};
