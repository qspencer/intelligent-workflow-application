import { Handle, Position, type Node, type NodeProps, type NodeTypes } from '@xyflow/react';

import { statusMeta, type CanvasNodeData } from '../../lib/canvas';

export type FlowNode = Node<CanvasNodeData, 'trigger' | 'deterministic' | 'agentic'>;

function Card({ data, kind }: { data: CanvasNodeData; kind: string }) {
  // When following an instance, the live status drives the left-border color
  // (overriding the node-type color) and adds a status pill.
  const status = data.status ? statusMeta(data.status) : null;
  const className = `canvas-node ${kind}${status ? ` status-${status.cssClass}` : ''}`;
  return (
    <div className={className}>
      <span className="node-icon" aria-hidden="true">
        {data.icon}
      </span>
      <div className="node-body">
        <div className="node-title" title={data.title}>
          {data.title}
        </div>
        <div className="node-subtitle" title={data.subtitle}>
          {data.subtitle}
        </div>
      </div>
      {status && (
        <span className={`node-status ${status.cssClass}`}>
          <span aria-hidden="true">{status.icon}</span> {status.label}
        </span>
      )}
    </div>
  );
}

function TriggerNode({ data }: NodeProps<FlowNode>) {
  return (
    <>
      <Card data={data} kind="trigger" />
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}

function DeterministicNode({ data }: NodeProps<FlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <Card data={data} kind="deterministic" />
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}

function AgenticNode({ data }: NodeProps<FlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <Card data={data} kind="agentic" />
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}

/** Stable reference — passed to <ReactFlow nodeTypes={...}/>. */
export const nodeTypes: NodeTypes = {
  trigger: TriggerNode,
  deterministic: DeterministicNode,
  agentic: AgenticNode,
};
