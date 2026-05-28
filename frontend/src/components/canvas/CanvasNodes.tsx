import { Handle, Position, type Node, type NodeProps, type NodeTypes } from '@xyflow/react';

import type { CanvasNodeData } from '../../lib/canvas';

export type FlowNode = Node<CanvasNodeData, 'trigger' | 'deterministic' | 'agentic'>;

function Card({ data, kind }: { data: CanvasNodeData; kind: string }) {
  return (
    <div className={`canvas-node ${kind}`}>
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
