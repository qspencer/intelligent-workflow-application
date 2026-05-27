import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { ApiService } from './api.service';

type HttpStub = {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
};

function makeService(): { service: ApiService; http: HttpStub } {
  const http: HttpStub = {
    get: vi.fn().mockReturnValue(of([])),
    post: vi.fn().mockReturnValue(of(null)),
  };
  // Bypass DI: inject() runs at construction, so we skip the constructor and
  // patch the private property directly. ApiService has no other state.
  const service = Object.create(ApiService.prototype) as ApiService;
  (service as unknown as { http: HttpStub }).http = http;
  return { service, http };
}

describe('ApiService', () => {
  it('listWorkflows hits /api/workflows', () => {
    const { service, http } = makeService();
    service.listWorkflows();
    expect(http.get).toHaveBeenCalledWith('/api/workflows');
  });

  it('listInstances strips empty / null params', () => {
    const { service, http } = makeService();
    service.listInstances({ workflow_id: 'wf-1', state: '', limit: 10 });
    expect(http.get).toHaveBeenCalledWith('/api/workflow-instances', {
      params: { workflow_id: 'wf-1', limit: '10' },
    });
  });

  it('listInstances with no args sends an empty params object', () => {
    const { service, http } = makeService();
    service.listInstances();
    expect(http.get).toHaveBeenCalledWith('/api/workflow-instances', {
      params: {},
    });
  });

  it('getInstance encodes the instance id into the path', () => {
    const { service, http } = makeService();
    service.getInstance('inst-abc');
    expect(http.get).toHaveBeenCalledWith('/api/workflow-instances/inst-abc');
  });

  it('instanceAudit hits the audit subpath', () => {
    const { service, http } = makeService();
    service.instanceAudit('inst-9');
    expect(http.get).toHaveBeenCalledWith('/api/workflow-instances/inst-9/audit');
  });

  it.each([
    ['pauseInstance', '/api/workflow-instances/x/pause'],
    ['resumeInstance', '/api/workflow-instances/x/resume'],
    ['retryInstance', '/api/workflow-instances/x/retry'],
    ['killInstance', '/api/workflow-instances/x/kill'],
  ] as const)('%s posts to %s with empty body', (method, expectedUrl) => {
    const { service, http } = makeService();
    (service[method] as (id: string) => unknown)('x');
    expect(http.post).toHaveBeenCalledWith(expectedUrl, {});
  });

  it('importWorkflow sends YAML with the right content-type', () => {
    const { service, http } = makeService();
    service.importWorkflow('id: x\nname: X', 'yaml');
    expect(http.post).toHaveBeenCalledWith(
      '/api/workflows/import',
      'id: x\nname: X',
      { headers: { 'Content-Type': 'application/x-yaml' } },
    );
  });

  it('importWorkflow sends JSON with application/json', () => {
    const { service, http } = makeService();
    service.importWorkflow('{"id":"x"}', 'json');
    expect(http.post).toHaveBeenCalledWith(
      '/api/workflows/import',
      '{"id":"x"}',
      { headers: { 'Content-Type': 'application/json' } },
    );
  });

  it('runWorkflow posts trigger payload to /workflows/{id}/run', () => {
    const { service, http } = makeService();
    service.runWorkflow('wf-1', { file_path: '/abs/foo.pdf' });
    expect(http.post).toHaveBeenCalledWith(
      '/api/workflows/wf-1/run',
      { file_path: '/abs/foo.pdf' },
    );
  });

  it('forkInstance posts from_step_id to /workflow-instances/{id}/fork', () => {
    const { service, http } = makeService();
    service.forkInstance('inst-9', 'classify');
    expect(http.post).toHaveBeenCalledWith(
      '/api/workflow-instances/inst-9/fork',
      { from_step_id: 'classify' },
    );
  });

  it.each([
    ['costByWorkflow', '/api/cost/by-workflow'],
    ['costByModel', '/api/cost/by-model'],
    ['costByDay', '/api/cost/by-day'],
  ] as const)('%s hits %s with empty params by default', (method, expectedUrl) => {
    const { service, http } = makeService();
    (service[method] as (since?: string) => unknown)();
    expect(http.get).toHaveBeenCalledWith(expectedUrl, { params: {} });
  });

  it('costByWorkflow passes through the since param when provided', () => {
    const { service, http } = makeService();
    service.costByWorkflow('2026-05-01T00:00:00Z');
    expect(http.get).toHaveBeenCalledWith('/api/cost/by-workflow', {
      params: { since: '2026-05-01T00:00:00Z' },
    });
  });
});
