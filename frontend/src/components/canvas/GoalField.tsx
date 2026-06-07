import { useState } from 'react';

/** Safer goal editing (C7.4). Reframes the raw agentic `goal` textarea as
 *  "the AI's instructions" with inline help + on-demand examples, so a
 *  non-technical author understands they're editing agent behavior. The
 *  structured "goal wizard" is deliberately deferred. */

const EXAMPLES = [
  'Classify the document as one of: invoice, receipt, contract, report, other. Return JSON with {category, summary, key_fields}.',
  'Read the PR diff, then write a 2-sentence summary and list any risky changes. Be concise.',
  'Summarize the email in one line and decide whether it needs a human reply (yes/no).',
];

export function GoalField({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const [showExamples, setShowExamples] = useState(false);
  return (
    <div className="goal-field">
      <div className="goal-label">
        <span className="rf-label">Instructions for the AI</span>
        <button
          type="button"
          className="link"
          aria-expanded={showExamples}
          onClick={() => setShowExamples((s) => !s)}
        >
          {showExamples ? 'Hide examples' : 'See examples'}
        </button>
      </div>
      <textarea
        rows={8}
        aria-label="AI instructions"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="goal-help">
        This is what the AI reads as its task on every run. Be specific: say what to produce, the
        output format (e.g. which JSON fields), and which inputs or tools to use. Editing this
        changes how the agent behaves.
      </p>
      {showExamples && (
        <ul className="goal-examples">
          {EXAMPLES.map((ex, i) => (
            <li key={i}>{ex}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
