// Mirror the backend Pydantic models — only the fields the UI consumes.

export interface TriggerSpec {
  type: string;
  config: Record<string, unknown>;
  /** Optional shape-hint for the dashboard's Run dialog. Workflow YAML
   *  authors declare a realistic payload here; the dialog pre-fills
   *  it as JSON so operators don't have to guess the schema. */
  example_payload?: Record<string, unknown> | null;
}

export interface StepRuntime {
  retries: number;
  timeout_seconds: number | null;
}

export interface DeterministicStep {
  id: string;
  type: 'deterministic';
  function: string;
  config: Record<string, unknown>;
  outputs: string[];
  capabilities: unknown | null;
  runtime: StepRuntime;
  /** UI-only: author-set node title + result-card renderer (canvas). */
  label?: string | null;
  output_renderer?: string | null;
}

export interface AgenticStepPolicy {
  max_iterations: number;
  max_total_tokens: number;
  inference_config: Record<string, unknown> | null;
}

export interface AgenticStep {
  id: string;
  type: 'agentic';
  goal: string;
  tools: string[];
  model: string;
  system_prompt: string | null;
  policy: AgenticStepPolicy;
  outputs: string[];
  capabilities: unknown | null;
  runtime: StepRuntime;
  /** UI-only: author-set node title + result-card renderer (canvas). */
  label?: string | null;
  output_renderer?: string | null;
}

export type WorkflowStep = DeterministicStep | AgenticStep;

/** Directed edge. The backend serializes the schema's `from`/`to` aliases. */
export interface WorkflowEdge {
  from: string;
  to: string;
  condition: string | null;
  /** UI-only: plain-language render of `condition` on the canvas. */
  condition_label?: string | null;
}

export interface WorkflowPolicy {
  max_total_tokens: number | null;
  timeout_seconds?: number | null;
  budget_action: 'notify' | 'pause' | 'escalate';
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  description: string;
  trigger?: TriggerSpec;
  /** Present on the single-workflow + list endpoints; the canvas needs them. */
  steps?: WorkflowStep[];
  edges?: WorkflowEdge[];
  policies?: WorkflowPolicy;
}

/** Pre-run cost context for the Run dialog (C6.2). Mirrors
 *  GET /api/workflows/{id}/cost-estimate. */
export interface CostEstimateModel {
  step_id: string;
  model: string;
  input_per_million: number | null;
  output_per_million: number | null;
}

export interface CostEstimate {
  workflow_id: string;
  models: CostEstimateModel[];
  run_count: number;
  avg_cost_usd: number | null;
  avg_tokens: number | null;
  max_total_tokens: number | null;
  budget_action: 'notify' | 'pause' | 'escalate';
}

/** Lightweight summary for the templates gallery (canvas C5.2). */
export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  step_count: number;
  trigger_type: string;
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
 *  a different key field name (workflow_id / model / date). */
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

/** Dev-only error capture (AUTH_MODE=dev). Mirrors api/dev.py. */
export interface DevError {
  fingerprint: string;
  level: string;
  logger: string;
  message: string;
  traceback: string | null;
  count: number;
  first_seen: string;
  last_seen: string;
}

export interface DevErrorsResponse {
  total: number;
  distinct: number;
  errors: DevError[];
}
