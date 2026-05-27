import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AuditEntry,
  CostRowByDay,
  CostRowByModel,
  CostRowByWorkflow,
  InstanceDetail,
  WorkflowDefinition,
  WorkflowInstance,
} from '../types';

const API_BASE = '/api';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  listWorkflows(): Observable<WorkflowDefinition[]> {
    return this.http.get<WorkflowDefinition[]>(`${API_BASE}/workflows`);
  }

  listInstances(params: {
    workflow_id?: string;
    state?: string;
    limit?: number;
  } = {}): Observable<WorkflowInstance[]> {
    return this.http.get<WorkflowInstance[]>(`${API_BASE}/workflow-instances`, {
      params: this.cleanParams(params),
    });
  }

  getInstance(id: string): Observable<InstanceDetail> {
    return this.http.get<InstanceDetail>(`${API_BASE}/workflow-instances/${id}`);
  }

  instanceAudit(id: string): Observable<AuditEntry[]> {
    return this.http.get<AuditEntry[]>(
      `${API_BASE}/workflow-instances/${id}/audit`,
    );
  }

  pauseInstance(id: string): Observable<unknown> {
    return this.http.post(`${API_BASE}/workflow-instances/${id}/pause`, {});
  }

  resumeInstance(id: string): Observable<unknown> {
    return this.http.post(`${API_BASE}/workflow-instances/${id}/resume`, {});
  }

  retryInstance(id: string): Observable<unknown> {
    return this.http.post(`${API_BASE}/workflow-instances/${id}/retry`, {});
  }

  killInstance(id: string): Observable<unknown> {
    return this.http.post(`${API_BASE}/workflow-instances/${id}/kill`, {});
  }

  /** Hard-delete a terminal (completed / failed / killed) instance.
   *  Backend rejects with 400 if the instance is still running / pending /
   *  paused; callers should hide the button in those states. Audit entries
   *  are not deleted (append-only). */
  deleteInstance(id: string): Observable<unknown> {
    return this.http.delete(`${API_BASE}/workflow-instances/${id}`);
  }

  importWorkflow(body: string, contentType: 'yaml' | 'json'): Observable<WorkflowDefinition> {
    const mime =
      contentType === 'json' ? 'application/json' : 'application/x-yaml';
    return this.http.post<WorkflowDefinition>(`${API_BASE}/workflows/import`, body, {
      headers: { 'Content-Type': mime },
    });
  }

  runWorkflow(
    workflowId: string,
    triggerPayload: Record<string, unknown>,
  ): Observable<{ status: string; instance_id: string; state: string }> {
    return this.http.post<{ status: string; instance_id: string; state: string }>(
      `${API_BASE}/workflows/${workflowId}/run`,
      triggerPayload,
    );
  }

  forkInstance(
    id: string,
    fromStepId: string,
  ): Observable<{
    status: string;
    source_instance_id: string;
    instance_id: string;
    state: string;
  }> {
    return this.http.post<{
      status: string;
      source_instance_id: string;
      instance_id: string;
      state: string;
    }>(`${API_BASE}/workflow-instances/${id}/fork`, { from_step_id: fromStepId });
  }

  costByWorkflow(since?: string): Observable<CostRowByWorkflow[]> {
    return this.http.get<CostRowByWorkflow[]>(`${API_BASE}/cost/by-workflow`, {
      params: this.cleanParams({ since }),
    });
  }

  costByModel(since?: string): Observable<CostRowByModel[]> {
    return this.http.get<CostRowByModel[]>(`${API_BASE}/cost/by-model`, {
      params: this.cleanParams({ since }),
    });
  }

  costByDay(since?: string): Observable<CostRowByDay[]> {
    return this.http.get<CostRowByDay[]>(`${API_BASE}/cost/by-day`, {
      params: this.cleanParams({ since }),
    });
  }

  private cleanParams(input: Record<string, unknown>): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(input)) {
      if (v !== undefined && v !== null && v !== '') out[k] = String(v);
    }
    return out;
  }
}
