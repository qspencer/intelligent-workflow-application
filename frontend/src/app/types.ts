// Mirror the backend Pydantic models — only the fields the UI consumes.

export interface WorkflowDefinition {
  id: string;
  name: string;
  description: string;
}

export type WorkflowState =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'killed';

export type StepState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped';

export interface WorkflowInstance {
  id: string;
  workflow_id: string;
  state: WorkflowState;
  trigger_payload: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface StepExecution {
  id: string;
  instance_id: string;
  step_id: string;
  state: StepState;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface InstanceDetail {
  instance: WorkflowInstance;
  steps: StepExecution[];
}

export interface AuditEntry {
  id: string;
  timestamp: string;
  actor_type: string;
  actor_id: string;
  action: string;
  workflow_instance_id: string | null;
  step_id: string | null;
  detail: Record<string, unknown>;
}

/** Backend cost-report row. Each endpoint returns the same shape with
 *  a different key field name (workflow_id / model / date). The frontend
 *  carries them as an indexed-access union for ergonomics. */
export interface CostRowByWorkflow {
  workflow_id: string;
  total_cost_usd: number;
  total_tokens: number;
  step_count: number;
}

export interface CostRowByModel {
  model: string;
  total_cost_usd: number;
  total_tokens: number;
  step_count: number;
}

export interface CostRowByDay {
  date: string;
  total_cost_usd: number;
  total_tokens: number;
  step_count: number;
}
