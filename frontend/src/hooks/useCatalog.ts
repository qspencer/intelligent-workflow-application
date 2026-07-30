import { useCallback, useEffect, useState } from 'react';

import { api, errorMessage } from '../api/client';
import type { WorkflowAttribution, WorkflowDefinition, WorkflowState } from '../types';

/** The merged Automations catalog's data contract (IA_PLAN §6):
 *  - definitions failing fails the page (error set, list null);
 *  - every enrichment degrades independently — attribution failure hides
 *    badges, counts failure renders "—" (null map, never a misleading 0),
 *    latest-run failure renders no state chip;
 *  - joins are by workflow id; sort is deterministic (name, then id). */
export interface CatalogData {
  definitions: WorkflowDefinition[] | null;
  attribution: Record<string, WorkflowAttribution> | null;
  counts: Record<string, number> | null;
  latest: Record<string, WorkflowState>;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useCatalog(): CatalogData {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[] | null>(null);
  const [attribution, setAttribution] = useState<Record<string, WorkflowAttribution> | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [latest, setLatest] = useState<Record<string, WorkflowState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const defs = await api.listWorkflows();
      defs.sort((a, b) => (a.name || '').localeCompare(b.name || '') || a.id.localeCompare(b.id));
      setDefinitions(defs);
      setError(null);
    } catch (err) {
      setError(errorMessage(err, 'Failed to load automations'));
      setLoading(false);
      return;
    } finally {
      setLoading(false);
    }
    try {
      setAttribution(await api.workflowAttribution());
    } catch {
      setAttribution(null);
    }
    try {
      setCounts(await api.workflowInstanceCounts());
    } catch {
      setCounts(null);
    }
    try {
      const recent = await api.listInstances({ limit: 200 });
      const byWorkflow: Record<string, WorkflowState> = {};
      for (const inst of recent) {
        if (!(inst.workflow_id in byWorkflow)) byWorkflow[inst.workflow_id] = inst.state;
      }
      setLatest(byWorkflow);
    } catch {
      setLatest({});
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { definitions, attribution, counts, latest, loading, error, refresh };
}
