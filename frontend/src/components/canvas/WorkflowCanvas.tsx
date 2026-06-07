import '@xyflow/react/dist/style.css';

import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type ReactFlowInstance,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { hasRole } from '../../lib/auth';
import {
  TRIGGER_NODE_ID,
  buildGraph,
  newStep,
  uniqueStepId,
  type CanvasNodeData,
} from '../../lib/canvas';
import { useEvents } from '../../hooks/useEvents';
import type {
  AuditEntry,
  CapabilityReport,
  StepExecution,
  ValidationFinding,
  WorkflowCatalog,
  WorkflowDefinition,
  WorkflowInstance,
} from '../../types';
import { CanvasFooter } from './CanvasFooter';
import { EditInspector } from './EditInspector';
import { Inspector } from './Inspector';
import { RunDialog } from './RunDialog';
import { ValidationPanel } from './ValidationPanel';
import { nodeTypes, type FlowNode } from './CanvasNodes';

export function WorkflowCanvas() {
  const { id = '' } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const instanceId = searchParams.get('instance');
  const following = instanceId !== null;

  const [def, setDef] = useState<WorkflowDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [instance, setInstance] = useState<WorkflowInstance | null>(null);
  const [steps, setSteps] = useState<StepExecution[]>([]);
  const [runOpen, setRunOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  // C6.3 capability boundary — best-effort; null if unavailable.
  const [caps, setCaps] = useState<CapabilityReport | null>(null);
  // C7.3 build-time validation findings (edit mode).
  const [findings, setFindings] = useState<ValidationFinding[]>([]);
  // C7.2 authoring catalog (triggers / functions / tools) — fetched once.
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);

  // ---- edit mode (C4 slice 1: edit fields + save) ----
  const [draft, setDraft] = useState<WorkflowDefinition | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const editing = draft !== null;
  // Edit is a definition-view, Designer/Admin affordance — not while
  // following a run, and not for read-only roles.
  const canEdit = !following && hasRole(['admins', 'designers']);
  const dirty = editing && def !== null && JSON.stringify(draft) !== JSON.stringify(def);

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // While editing, the graph is built from the draft; otherwise from def.
  const activeDef = editing ? draft : def;
  // Only the *structure* (node ids, edges, trigger type) should trigger a
  // re-layout — field edits must not rebuild (they'd drop selection mid-typing).
  const structureKey = useMemo(() => {
    if (!activeDef) return '';
    return JSON.stringify({
      n: (activeDef.steps ?? []).map((s) => s.id),
      e: (activeDef.edges ?? []).map((e) => `${e.from}>${e.to}`),
      t: activeDef.trigger?.type ?? '',
    });
  }, [activeDef]);

  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;
  const rfRef = useRef<ReactFlowInstance<FlowNode, Edge> | null>(null);

  // Re-fit whenever the structure changes (add/delete/connect, or a save that
  // grows the graph) so newly-added nodes are always brought into view.
  useEffect(() => {
    const t = setTimeout(() => rfRef.current?.fitView({ padding: 0.2, duration: 200 }), 60);
    return () => clearTimeout(t);
  }, [structureKey]);

  // ---- definition (the graph shape) ----
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSelectedId(null);
    api
      .getWorkflow(id)
      .then((d) => {
        if (!cancelled) {
          setDef(d);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, 'Failed to load workflow'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // ---- capability boundary (C6.3), best-effort ----
  useEffect(() => {
    let cancelled = false;
    api
      .getCapabilities(id)
      .then((c) => !cancelled && setCaps(c))
      .catch(() => !cancelled && setCaps(null));
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Rebuild + re-layout on structural change (or def load/save / mode flip).
  // Field edits change `draft` identity but not `structureKey`, so they don't
  // rebuild — selection is preserved by re-marking the selected node.
  useEffect(() => {
    const source = editing ? draft : def;
    if (!source) return;
    const graph = buildGraph(source);
    const sel = selectedIdRef.current;
    setNodes(graph.nodes.map((n) => (n.id === sel ? { ...n, selected: true } : n)) as FlowNode[]);
    setEdges(graph.edges as Edge[]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey, editing, def, setNodes, setEdges]);

  // ---- authoring catalog (C7.2), fetched once for the edit pickers ----
  useEffect(() => {
    let cancelled = false;
    api
      .getCatalog()
      .then((c) => !cancelled && setCatalog(c))
      .catch(() => !cancelled && setCatalog(null));
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- build-time validation (C7.3), edit mode, debounced on draft edits ----
  useEffect(() => {
    if (!editing || !draft) {
      setFindings([]);
      return;
    }
    const t = setTimeout(() => {
      api
        .validateWorkflow(draft)
        .then((r) => setFindings(r.findings))
        .catch(() => setFindings([]));
    }, 400);
    return () => clearTimeout(t);
  }, [editing, draft]);

  const errorNodeIds = useMemo(
    () =>
      new Set(
        findings.filter((f) => f.level === 'error' && f.node_id).map((f) => f.node_id as string),
      ),
    [findings],
  );

  // Re-mark invalid nodes (red border) without forcing a structural rebuild.
  useEffect(() => {
    setNodes(
      (nds) =>
        nds.map((n) => ({
          ...n,
          className: errorNodeIds.has(n.id) ? 'rf-invalid' : undefined,
        })) as FlowNode[],
    );
  }, [errorNodeIds, setNodes]);

  const errorCount = findings.filter((f) => f.level === 'error').length;

  // ---- instance (live status), only when ?instance=<id> ----
  const refreshInstance = useCallback(async () => {
    if (!instanceId) return;
    try {
      const detail = await api.getInstance(instanceId);
      setInstance(detail.instance);
      setSteps(detail.steps);
    } catch {
      // Transient errors are tolerated; the next poll retries.
    }
  }, [instanceId]);

  useEffect(() => {
    if (!instanceId) {
      setInstance(null);
      setSteps([]);
      return;
    }
    void refreshInstance();
    const timer = setInterval(() => void refreshInstance(), 2500);
    return () => clearInterval(timer);
  }, [instanceId, refreshInstance]);

  // Refresh immediately on any audit event for this instance (real-time feel).
  useEvents(
    useCallback(
      (entry: AuditEntry) => {
        if (entry.workflow_instance_id === instanceId) void refreshInstance();
      },
      [instanceId, refreshInstance],
    ),
    { enabled: following },
  );

  // Patch live status onto existing nodes (preserves position + selection).
  const stepStateById = useMemo(() => {
    const m: Record<string, StepExecution['state']> = {};
    for (const s of steps) m[s.step_id] = s.state;
    return m;
  }, [steps]);

  useEffect(() => {
    if (!following) return;
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: { ...n.data, status: stepStateById[n.id] },
      })),
    );
  }, [stepStateById, following, setNodes]);

  const selectedData: CanvasNodeData | null = useMemo(() => {
    const n = nodes.find((node) => node.id === selectedId);
    return n ? n.data : null;
  }, [nodes, selectedId]);

  const selectedExecution: StepExecution | null = useMemo(() => {
    if (!following || !selectedId) return null;
    return steps.find((s) => s.step_id === selectedId) ?? null;
  }, [following, selectedId, steps]);

  function startEdit(): void {
    if (!def) return;
    setDraft(structuredClone(def));
    setSaveError(null);
  }

  // Arriving from "Create" / "Use this template" lands here with ?edit=1 —
  // drop straight into edit mode so a freshly-minted workflow is editable
  // without a second click. Only once def has loaded and the role allows it.
  useEffect(() => {
    if (searchParams.get('edit') === '1' && canEdit && def && draft === null) {
      setDraft(structuredClone(def));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [def, canEdit]);
  function discardEdit(): void {
    setDraft(null);
    setSaveError(null);
  }
  async function save(): Promise<void> {
    if (!draft) return;
    if (errorCount > 0) {
      setSaveError(`Fix ${errorCount} validation error${errorCount === 1 ? '' : 's'} before saving.`);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      // import returns {status, workflow_id}, not the definition — re-fetch
      // the canonical server state so the graph rebuilds correctly.
      await api.importWorkflow(JSON.stringify(draft), 'json');
      const fresh = await api.getWorkflow(id);
      setDef(fresh);
      setDraft(null);
    } catch (err) {
      setSaveError(errorMessage(err, 'Save failed'));
    } finally {
      setSaving(false);
    }
  }

  // ---- structural edits (C4 slice 2) ----
  function addStep(type: 'deterministic' | 'agentic'): void {
    setDraft((d) => {
      if (!d) return d;
      const id = uniqueStepId((d.steps ?? []).map((s) => s.id), type === 'agentic' ? 'ai' : 'step');
      return { ...d, steps: [...(d.steps ?? []), newStep(type, id)] };
    });
  }
  function deleteStep(stepId: string): void {
    setDraft((d) => {
      if (!d) return d;
      return {
        ...d,
        steps: (d.steps ?? []).filter((s) => s.id !== stepId),
        edges: (d.edges ?? []).filter((e) => e.from !== stepId && e.to !== stepId),
      };
    });
    setSelectedId(null);
  }
  // Drag a connection between two step handles (bonus path; the Inspector's
  // form is the primary, accessible way to wire edges). Synthetic trigger
  // edges aren't real workflow edges, so connections touching it are ignored.
  const onConnect = useCallback((c: Connection) => {
    if (!c.source || !c.target) return;
    if (c.source === TRIGGER_NODE_ID || c.target === TRIGGER_NODE_ID) return;
    if (c.source === c.target) return;
    setDraft((d) => {
      if (!d) return d;
      const exists = (d.edges ?? []).some((e) => e.from === c.source && e.to === c.target);
      if (exists) return d;
      return { ...d, edges: [...(d.edges ?? []), { from: c.source, to: c.target, condition: null }] };
    });
  }, []);

  return (
    <div className="page-canvas">
      <div className="canvas-header">
        <div>
          <Link to={following ? `/canvas/${id}` : '/workflows'}>
            {following ? '← Definition view' : '← All workflows'}
          </Link>
          <h2>{def?.name ?? id}</h2>
        </div>
        <div className="canvas-header-actions">
          {editing ? (
            <>
              {saveError && <span className="error">{saveError}</span>}
              <button onClick={discardEdit} disabled={saving}>
                Discard
              </button>
              <button className="primary" onClick={() => void save()} disabled={saving || !dirty}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          ) : (
            <>
              {!following && def && (
                <>
                  <button className="primary" onClick={() => setRunOpen(true)}>
                    Run
                  </button>
                  <button onClick={() => setTestOpen(true)} title="Dry-run in a sandbox">
                    Test
                  </button>
                </>
              )}
              {canEdit && def && <button onClick={startEdit}>Edit</button>}
            </>
          )}
          <span className="mode-pill">
            {editing ? 'Editing' : following ? 'Live run' : 'View only'}
          </span>
        </div>
      </div>

      {runOpen && def && <RunDialog def={def} onClose={() => setRunOpen(false)} />}
      {testOpen && def && <RunDialog def={def} dryRun onClose={() => setTestOpen(false)} />}

      {following && searchParams.get('dry') === '1' && (
        <div className="dry-banner">
          🧪 Dry run — sandboxed (mock world; email/connector/browser tools disabled, live AI).
          Nothing real was touched.
        </div>
      )}

      {editing && <ValidationPanel findings={findings} onSelect={setSelectedId} />}

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
        <>
          {editing && (
            <div className="canvas-palette">
              <span className="cp-label">Add step:</span>
              <button onClick={() => addStep('deterministic')}>+ Function step</button>
              <button onClick={() => addStep('agentic')}>+ AI step</button>
              <span className="cp-hint">
                then drag between node handles, or use the inspector to connect.
              </span>
            </div>
          )}
          <div className="canvas-shell">
            <div className="canvas-flow">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onInit={(inst) => {
                  rfRef.current = inst;
                }}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                onPaneClick={() => setSelectedId(null)}
                onConnect={onConnect}
                nodesDraggable={false}
                nodesConnectable={editing}
                edgesFocusable={false}
                deleteKeyCode={null}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                proOptions={{ hideAttribution: true }}
              >
                <Background />
                <Controls showInteractive={false} />
                {/* Only worthwhile for large graphs; on small ones it just
                    overlaps (and intercepts clicks on) bottom-corner nodes. */}
                {nodes.length > 12 && <MiniMap pannable zoomable />}
              </ReactFlow>
            </div>
            {editing && draft ? (
              <EditInspector
                draft={draft}
                selectedId={selectedId}
                onChange={setDraft}
                onDeleteStep={deleteStep}
                catalog={catalog}
              />
            ) : (
              <Inspector
                data={selectedData}
                execution={following ? selectedExecution : undefined}
                capability={caps?.steps.find((s) => s.step_id === selectedId) ?? null}
              />
            )}
          </div>
          {following && instance && (
            <CanvasFooter instance={instance} steps={steps} policy={def?.policies ?? null} />
          )}
        </>
      )}
    </div>
  );
}
