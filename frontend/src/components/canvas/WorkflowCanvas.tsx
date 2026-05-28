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
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { api, errorMessage } from '../../api/client';
import { buildGraph, type CanvasNodeData } from '../../lib/canvas';
import type { WorkflowDefinition } from '../../types';
import { Inspector } from './Inspector';
import { nodeTypes, type FlowNode } from './CanvasNodes';

export function WorkflowCanvas() {
  const { id = '' } = useParams<{ id: string }>();

  const [def, setDef] = useState<WorkflowDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getWorkflow(id)
      .then((d) => {
        if (cancelled) return;
        setDef(d);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(errorMessage(err, 'Failed to load workflow'));
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

  const selectedData: CanvasNodeData | null = useMemo(() => {
    const n = nodes.find((node) => node.id === selectedId);
    return n ? n.data : null;
  }, [nodes, selectedId]);

  return (
    <div className="page-canvas">
      <div className="canvas-header">
        <div>
          <Link to="/workflows">← All workflows</Link>
          <h2>{def?.name ?? id}</h2>
        </div>
        <span className="mode-pill">View only</span>
      </div>

      {loading ? (
        <p>Loading…</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
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
          <Inspector data={selectedData} />
        </div>
      )}
    </div>
  );
}
