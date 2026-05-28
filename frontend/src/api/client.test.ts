import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api, errorMessage } from './client';

interface FakeResponse {
  ok: boolean;
  status: number;
  text: () => Promise<string>;
  json: () => Promise<unknown>;
}

function okJson(body: unknown): FakeResponse {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
    json: async () => body,
  };
}

function errJson(status: number, body: unknown): FakeResponse {
  return {
    ok: false,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error('fetch was not called');
  return call as [string, RequestInit];
}

beforeEach(() => {
  localStorage.clear();
  fetchMock = vi.fn().mockResolvedValue(okJson([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

describe('api URL + method construction', () => {
  it('listWorkflows → GET /api/workflows', async () => {
    await api.listWorkflows();
    const [url, init] = lastCall();
    expect(url).toBe('/api/workflows');
    expect(init.method).toBe('GET');
  });

  it('listInstances strips empty/null params', async () => {
    await api.listInstances({ workflow_id: 'wf-1', state: '', limit: 10 });
    expect(lastCall()[0]).toBe('/api/workflow-instances?workflow_id=wf-1&limit=10');
  });

  it('listInstances with no args sends no query string', async () => {
    await api.listInstances();
    expect(lastCall()[0]).toBe('/api/workflow-instances');
  });

  it('getInstance / instanceAudit encode the path', async () => {
    await api.getInstance('inst-abc');
    expect(lastCall()[0]).toBe('/api/workflow-instances/inst-abc');
    await api.instanceAudit('inst-9');
    expect(lastCall()[0]).toBe('/api/workflow-instances/inst-9/audit');
  });

  it.each([
    ['pauseInstance', '/api/workflow-instances/x/pause'],
    ['resumeInstance', '/api/workflow-instances/x/resume'],
    ['retryInstance', '/api/workflow-instances/x/retry'],
    ['killInstance', '/api/workflow-instances/x/kill'],
  ] as const)('%s POSTs to %s', async (method, expected) => {
    await api[method]('x');
    const [url, init] = lastCall();
    expect(url).toBe(expected);
    expect(init.method).toBe('POST');
  });

  it('importWorkflow sends YAML with the right content-type', async () => {
    await api.importWorkflow('id: x\nname: X', 'yaml');
    const [url, init] = lastCall();
    expect(url).toBe('/api/workflows/import');
    expect(init.method).toBe('POST');
    expect(init.body).toBe('id: x\nname: X');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/x-yaml',
    );
  });

  it('importWorkflow sends JSON with application/json', async () => {
    await api.importWorkflow('{"id":"x"}', 'json');
    expect((lastCall()[1].headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );
  });

  it('runWorkflow POSTs the payload as JSON', async () => {
    await api.runWorkflow('wf-1', { file_path: '/abs/foo.pdf' });
    const [url, init] = lastCall();
    expect(url).toBe('/api/workflows/wf-1/run');
    expect(init.body).toBe(JSON.stringify({ file_path: '/abs/foo.pdf' }));
  });

  it('forkInstance POSTs from_step_id', async () => {
    await api.forkInstance('inst-9', 'classify');
    const [url, init] = lastCall();
    expect(url).toBe('/api/workflow-instances/inst-9/fork');
    expect(init.body).toBe(JSON.stringify({ from_step_id: 'classify' }));
  });

  it('deleteInstance issues DELETE', async () => {
    await api.deleteInstance('inst-9');
    const [url, init] = lastCall();
    expect(url).toBe('/api/workflow-instances/inst-9');
    expect(init.method).toBe('DELETE');
  });

  it('deleteInstancesByStates repeats the state param', async () => {
    await api.deleteInstancesByStates(['completed', 'failed', 'killed']);
    expect(lastCall()[0]).toBe(
      '/api/workflow-instances?state=completed&state=failed&state=killed',
    );
  });

  it('deleteInstancesByStates appends workflow_id when given', async () => {
    await api.deleteInstancesByStates(['completed'], 'wf-42');
    expect(lastCall()[0]).toBe(
      '/api/workflow-instances?state=completed&workflow_id=wf-42',
    );
  });

  it.each([
    ['costByWorkflow', '/api/cost/by-workflow'],
    ['costByModel', '/api/cost/by-model'],
    ['costByDay', '/api/cost/by-day'],
  ] as const)('%s → %s with no since param', async (method, expected) => {
    await api[method]();
    expect(lastCall()[0]).toBe(expected);
  });

  it('costByWorkflow passes through the since param', async () => {
    await api.costByWorkflow('2026-05-01T00:00:00Z');
    expect(lastCall()[0]).toBe(
      '/api/cost/by-workflow?since=2026-05-01T00%3A00%3A00Z',
    );
  });

  it('injects dev-auth headers from localStorage', async () => {
    localStorage.setItem('wp.user', 'alice');
    localStorage.setItem('wp.groups', 'auditors');
    await api.listWorkflows();
    const headers = lastCall()[1].headers as Record<string, string>;
    expect(headers['X-Dev-User']).toBe('alice');
    expect(headers['X-Dev-Groups']).toBe('auditors');
  });
});

describe('error handling', () => {
  it('rejects with an ApiError carrying the backend detail', async () => {
    fetchMock.mockResolvedValueOnce(errJson(400, { detail: 'bad payload' }));
    await expect(api.runWorkflow('wf-1', {})).rejects.toBeInstanceOf(ApiError);
  });

  it('errorMessage surfaces the detail string', async () => {
    fetchMock.mockResolvedValueOnce(errJson(400, { detail: 'bad payload' }));
    try {
      await api.runWorkflow('wf-1', {});
      throw new Error('expected rejection');
    } catch (err) {
      expect(errorMessage(err, 'fallback')).toBe('bad payload');
    }
  });

  it('errorMessage falls back for unknown throwables', () => {
    expect(errorMessage('weird', 'fallback')).toBe('fallback');
  });
});
