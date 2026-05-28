import { TRIGGER_NODE_ID, isAgentic } from '../../lib/canvas';
import { removeAt, setIn, type Path } from '../../lib/run-form';
import type { WorkflowDefinition } from '../../types';
import { FieldEditor } from './FieldEditor';

/**
 * Editable Inspector (C4). Edits the selected step or trigger against an
 * in-memory draft definition; the parent saves the draft via the import API.
 * Structural edits (add/remove steps, connect/disconnect) are a later slice —
 * this edits the fields of existing nodes only.
 */
export function EditInspector({
  draft,
  selectedId,
  onChange,
}: {
  draft: WorkflowDefinition;
  selectedId: string | null;
  onChange: (next: WorkflowDefinition) => void;
}) {
  if (!selectedId) {
    return (
      <aside className="canvas-inspector">
        <p className="muted">Select a step or the trigger to edit it.</p>
      </aside>
    );
  }

  if (selectedId === TRIGGER_NODE_ID) {
    const trigger = draft.trigger;
    if (!trigger) return <aside className="canvas-inspector" />;
    const update = (path: Path, value: unknown) =>
      onChange({ ...draft, trigger: setIn(trigger, path, value) as typeof trigger });
    const remove = (path: Path) =>
      onChange({ ...draft, trigger: removeAt(trigger, path) as typeof trigger });
    return (
      <aside className="canvas-inspector">
        <h3>Edit trigger</h3>
        <label className="rf-field">
          <span className="rf-label">type</span>
          <input
            type="text"
            value={trigger.type}
            onChange={(e) => update(['type'], e.target.value)}
          />
        </label>
        <div className="field">
          <span className="field-label">config</span>
          <FieldEditor label="" value={trigger.config ?? {}} path={['config']} update={update} remove={remove} />
        </div>
      </aside>
    );
  }

  const idx = draft.steps?.findIndex((s) => s.id === selectedId) ?? -1;
  const step = idx >= 0 ? draft.steps![idx] : undefined;
  if (!step) return <aside className="canvas-inspector" />;

  const update = (path: Path, value: unknown) => {
    const nextStep = setIn(step, path, value);
    const steps = [...(draft.steps ?? [])];
    steps[idx] = nextStep as typeof step;
    onChange({ ...draft, steps });
  };
  const remove = (path: Path) => {
    const nextStep = removeAt(step, path);
    const steps = [...(draft.steps ?? [])];
    steps[idx] = nextStep as typeof step;
    onChange({ ...draft, steps });
  };

  return (
    <aside className="canvas-inspector">
      <h3>
        Edit <code>{step.id}</code>
      </h3>

      {isAgentic(step) ? (
        <>
          <label className="rf-field">
            <span className="rf-label">model</span>
            <input
              type="text"
              value={step.model}
              onChange={(e) => update(['model'], e.target.value)}
            />
          </label>
          <label className="rf-field">
            <span className="rf-label">goal (instructions)</span>
            <textarea rows={8} value={step.goal} onChange={(e) => update(['goal'], e.target.value)} />
          </label>
          <label className="rf-field">
            <span className="rf-label">max_iterations</span>
            <input
              type="number"
              value={step.policy.max_iterations}
              onChange={(e) => update(['policy', 'max_iterations'], Number(e.target.value) || 0)}
            />
          </label>
          <label className="rf-field">
            <span className="rf-label">max_total_tokens</span>
            <input
              type="number"
              value={step.policy.max_total_tokens}
              onChange={(e) => update(['policy', 'max_total_tokens'], Number(e.target.value) || 0)}
            />
          </label>
          <div className="field">
            <span className="field-label">tools</span>
            <FieldEditor label="" value={step.tools} path={['tools']} update={update} remove={remove} />
          </div>
        </>
      ) : (
        <>
          <label className="rf-field">
            <span className="rf-label">function</span>
            <input
              type="text"
              value={step.function}
              onChange={(e) => update(['function'], e.target.value)}
            />
          </label>
          <div className="field">
            <span className="field-label">config</span>
            <FieldEditor label="" value={step.config} path={['config']} update={update} remove={remove} />
          </div>
        </>
      )}
    </aside>
  );
}
