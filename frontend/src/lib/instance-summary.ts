import type { WorkflowInstance } from '../types';

export interface InstanceSummary {
  /** What the run was about — e.g. an email's subject, a file path. */
  subject: string | null;
  /** A triage-style classification, when a step recorded one (`category`
   *  with `parse_ok` true — the shape all `record_*_triage` functions emit). */
  category: string | null;
}

/** At-a-glance result for the Instances list: pull the human-meaningful bits
 *  out of a run without clicking into it. Purely presentational — absent
 *  fields render as nothing. */
export function instanceSummary(inst: WorkflowInstance): InstanceSummary {
  const trigger = inst.trigger_payload ?? {};
  const subject =
    firstString(trigger, ['subject', 'file_path', 'title']) ?? null;

  let category: string | null = null;
  const steps = (inst.context as Record<string, unknown> | undefined)?.['steps'];
  if (steps && typeof steps === 'object') {
    for (const out of Object.values(steps as Record<string, unknown>)) {
      if (
        out &&
        typeof out === 'object' &&
        (out as Record<string, unknown>)['parse_ok'] === true &&
        typeof (out as Record<string, unknown>)['category'] === 'string'
      ) {
        category = (out as Record<string, unknown>)['category'] as string;
        break;
      }
    }
  }
  return { subject, category };
}

function firstString(obj: Record<string, unknown>, keys: string[]): string | undefined {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === 'string' && v.trim() !== '') return v;
  }
  return undefined;
}
