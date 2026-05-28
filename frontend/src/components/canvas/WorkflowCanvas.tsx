import '@xyflow/react/dist/style.css';

import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
} from '@xyflow/react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { buildGraph, type CanvasNodeData } from '../../lib/canvas';
import { useEvents } from '../../hooks/useEvents';
import type {
  AuditEntry,
  StepExecution,
  WorkflowDefinition,
  WorkflowInstance,
} from '../../types';
import { CanvasFooter } from './CanvasFooter';
import { Inspector } from './Inspector';
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

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // ---- definition (the graph shape) ----
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
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

  useEffect(() => {
    if (!def) return;
    const graph = buildGraph(def);
    setNodes(graph.nodes as FlowNode[]);
    setEdges(graph.edges as Edge[]);
    setSelectedId(null);
  }, [def, setNodes, setEdges]);

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

  return (
    <div className="page-canvas">
      <div className="canvas-header">
        <div>
          <Link to={following ? `/canvas/${id}` : '/workflows'}>
            {following ? '← Definition view' : '← All workflows'}
          </Link>
          <h2>{def?.name ?? id}</h2>
        </div>
        <span className="mode-pill">{following ? 'Live run' : 'View only'}</span>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
        <>
          <div className="canvas-shell">
            <div className="canvas-flow">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                onPaneClick={() => setSelectedId(null)}
                nodesDraggable={false}
                nodesConnectable={false}
                edgesFocusable={false}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                proOptions={{ hideAttribution: true }}
              >
                <Background />
                <Controls showInteractive={false} />
                <MiniMap pannable zoomable />
              </ReactFlow>
            </div>
            <Inspector
              data={selectedData}
              execution={following ? selectedExecution : undefined}
            />
          </div>
          {following && instance && <CanvasFooter instance={instance} steps={steps} />}
        </>
      )}
    </div>
  );
}
