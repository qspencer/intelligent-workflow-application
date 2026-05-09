import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AuditEntry,
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

  private cleanParams(input: Record<string, unknown>): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(input)) {
      if (v !== undefined && v !== null && v !== '') out[k] = String(v);
    }
    return out;
  }
}
