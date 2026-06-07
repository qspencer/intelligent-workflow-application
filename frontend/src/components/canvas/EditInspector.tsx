import { TRIGGER_NODE_ID, isAgentic } from '../../lib/canvas';
import { removeAt, setIn, type Path } from '../../lib/run-form';
import type { WorkflowCatalog, WorkflowDefinition } from '../../types';
import { FieldEditor } from './FieldEditor';
import { GoalField } from './GoalField';
import { ToolPicker } from './ToolPicker';

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
  onDeleteStep,
  catalog,
}: {
  draft: WorkflowDefinition;
  selectedId: string | null;
  onChange: (next: WorkflowDefinition) => void;
  onDeleteStep: (stepId: string) => void;
  catalog?: WorkflowCatalog | null;
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
    const triggerCatalog = catalog?.triggers ?? [];
    const selectedTrigger = triggerCatalog.find((t) => t.type === trigger.type);
    return (
      <aside className="canvas-inspector">
        <h3>Edit trigger</h3>
        <label className="rf-field">
          <span className="rf-label">type</span>
          {triggerCatalog.length > 0 ? (
            <select value={trigger.type} onChange={(e) => update(['type'], e.target.value)}>
              {!selectedTrigger && <option value={trigger.type}>{trigger.type}</option>}
              {triggerCatalog.map((t) => (
                <option key={t.type} value={t.type}>
                  {t.label} ({t.type})
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={trigger.type}
              onChange={(e) => update(['type'], e.target.value)}
            />
          )}
        </label>
        {selectedTrigger && <p className="picker-desc">{selectedTrigger.description}</p>}
        {selectedTrigger && selectedTrigger.config_fields.length > 0 && (
          <ul className="picker-hints">
            {selectedTrigger.config_fields.map((f) => (
              <li key={f.name}>
                <code>{f.name}</code>
                {f.required && <span className="req"> *</span>} — {f.description}
              </li>
            ))}
          </ul>
        )}
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

  // Outgoing connections from this step + the steps it isn't yet wired to.
  const outgoing = (draft.edges ?? []).filter((e) => e.from === step.id);
  const connectable = (draft.steps ?? []).filter(
    (s) => s.id !== step.id && !outgoing.some((e) => e.to === s.id),
  );
  const addEdge = (to: string) =>
    onChange({ ...draft, edges: [...(draft.edges ?? []), { from: step.id, to, condition: null }] });
  const removeEdge = (to: string) =>
    onChange({
      ...draft,
      edges: (draft.edges ?? []).filter((e) => !(e.from === step.id && e.to === to)),
    });

  return (
    <aside className="canvas-inspector">
      <h3>
        Edit <code>{step.id}</code>
      </h3>

      <label className="rf-field">
        <span className="rf-label">label (node title — optional)</span>
        <input
          type="text"
          value={step.label ?? ''}
          placeholder="(derived from goal / function)"
          onChange={(e) => update(['label'], e.target.value || null)}
        />
      </label>
      <label className="rf-field">
        <span className="rf-label">result card</span>
        <select
          value={step.output_renderer ?? ''}
          onChange={(e) => update(['output_renderer'], e.target.value || null)}
        >
          <option value="">Auto</option>
          <option value="triage">Triage</option>
          <option value="eval">Evaluation</option>
          <option value="raw_json">Raw JSON</option>
        </select>
      </label>

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
          <GoalField value={step.goal} onChange={(v) => update(['goal'], v)} />
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
            {catalog?.tools ? (
              <ToolPicker
                tools={catalog.tools}
                selected={step.tools}
                onChange={(next) => update(['tools'], next)}
              />
            ) : (
              <FieldEditor label="" value={step.tools} path={['tools']} update={update} remove={remove} />
            )}
          </div>
        </>
      ) : (
        <>
          <label className="rf-field">
            <span className="rf-label">function</span>
            {catalog?.functions ? (
              <select value={step.function} onChange={(e) => update(['function'], e.target.value)}>
                {!catalog.functions.some((f) => f.name === step.function) && (
                  <option value={step.function}>{step.function}</option>
                )}
                {catalog.functions.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={step.function}
                onChange={(e) => update(['function'], e.target.value)}
              />
            )}
          </label>
          {(() => {
            const fn = catalog?.functions?.find((f) => f.name === step.function);
            return fn?.description ? <p className="picker-desc">{fn.description}</p> : null;
          })()}
          <div className="field">
            <span className="field-label">config</span>
            <FieldEditor label="" value={step.config} path={['config']} update={update} remove={remove} />
          </div>
        </>
      )}

      <div className="field">
        <span className="field-label">Connections (runs after this step)</span>
        {outgoing.length === 0 ? (
          <span className="muted">No outgoing connections.</span>
        ) : (
          <ul className="conn-list">
            {outgoing.map((e) => (
              <li key={e.to}>
                → <code>{e.to}</code>
                <button type="button" className="link" onClick={() => removeEdge(e.to)}>
                  remove
                </button>
              </li>
            ))}
          </ul>
        )}
        {connectable.length > 0 && (
          <select
            className="conn-add"
            value=""
            onChange={(ev) => ev.target.value && addEdge(ev.target.value)}
          >
            <option value="">+ Connect to…</option>
            {connectable.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id}
              </option>
            ))}
          </select>
        )}
      </div>

      <button type="button" className="danger" onClick={() => onDeleteStep(step.id)}>
        Delete step
      </button>
    </aside>
  );
}
