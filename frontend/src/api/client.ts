import { authHeaders } from '../lib/auth';
import type {
  AuditEntry,
  CostRowByDay,
  CostRowByModel,
  CostRowByWorkflow,
  DevErrorsResponse,
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

  /** Dev-only: recent backend ERROR logs (404s when not running AUTH_MODE=dev). */
  getDevErrors(): Promise<DevErrorsResponse> {
    return request('GET', '/dev/errors');
  },

  /** Dev-only: clear the captured-error buffer. */
  clearDevErrors(): Promise<{ status: string }> {
    return postJson('/dev/errors/clear', {});
  },
};
