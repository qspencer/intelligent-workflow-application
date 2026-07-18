"""Cost reports — aggregations across recent step executions.

Pulls completed agentic step executions from the repo and groups by workflow,
day, or model. The cost is read from `step.output["cost_usd"]` (computed by the
engine at step-completion time), so reports don't need to recompute pricing.

Dry runs (C6.1) use live Bedrock, so their spend is real money — but their
behavior isn't representative (external tools are stubbed). The views treat
them accordingly:
- `by_workflow` attributes dry-run spend to a separate `<id> (dry-run)` key, so
  the real per-workflow series stays clean while the spend stays visible.
- `run_stats_for_workflow` (the C6.2 pre-run estimate) excludes dry runs — an
  estimate should reflect real runs only.
- `by_model` / `by_day` include them: those are "what did we actually spend"
  views, and a dry-run token is a real token.

For Postgres deployments at scale, replace these Python aggregations with
single-pass SQL queries — Phase 3 work.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from workflow_platform.persistence import Repositories, StepExecutionState


@dataclass
class CostRow:
    key: str
    total_cost_usd: float
    total_tokens: int
    step_count: int


@dataclass
class WorkflowRunStats:
    """Aggregate cost over recent runs of one workflow, used for the C6.2
    pre-run estimate. `avg_*` are None when there's no history to average."""

    run_count: int
    total_cost_usd: float
    total_tokens: int

    @property
    def avg_cost_usd(self) -> float | None:
        return round(self.total_cost_usd / self.run_count, 6) if self.run_count else None

    @property
    def avg_tokens(self) -> int | None:
        return round(self.total_tokens / self.run_count) if self.run_count else None


class CostReportService:
    def __init__(self, repositories: Repositories, *, sample_limit: int = 5000) -> None:
        self.repositories = repositories
        self.sample_limit = sample_limit

    async def _sample(
        self, since: datetime | None, org_id: str | None = None
    ) -> list[tuple[str, dict[str, Any], bool]]:
        """Return (workflow_id, output_dict, is_dry_run) tuples for recent
        COMPLETED steps that have a `cost_usd` field. Skips deterministic /
        failed / skipped steps and steps without usage."""
        executions = await self.repositories.steps.list_recent(
            limit=self.sample_limit, since=since, org_id=org_id
        )
        sampled: list[tuple[str, dict[str, Any], bool]] = []
        instance_info: dict[str, tuple[str, bool]] = {}
        for exe in executions:
            if exe.state != StepExecutionState.COMPLETED:
                continue
            output = exe.output or {}
            if "cost_usd" not in output:
                continue
            info = instance_info.get(exe.instance_id)
            if info is None:
                instance = await self.repositories.instances.get(exe.instance_id)
                if instance is None:
                    continue
                info = (instance.workflow_id, bool((instance.context or {}).get("dry_run")))
                instance_info[exe.instance_id] = info
            sampled.append((info[0], output, info[1]))
        return sampled

    async def by_workflow(
        self, since: datetime | None = None, org_id: str | None = None
    ) -> list[CostRow]:
        return _group(
            [
                (f"{workflow_id} (dry-run)" if dry else workflow_id, output)
                for workflow_id, output, dry in await self._sample(since, org_id)
            ]
        )

    async def run_stats_for_workflow(
        self, workflow_id: str, since: datetime | None = None
    ) -> WorkflowRunStats:
        """Cost/token totals + distinct-run count for one workflow over recent
        COMPLETED agentic steps. `run_count` counts distinct instances seen in
        the same sample, so `avg_*` is a consistent per-run figure."""
        executions = await self.repositories.steps.list_recent(limit=self.sample_limit, since=since)
        instance_info: dict[str, tuple[str, bool]] = {}
        instances_seen: set[str] = set()
        total_cost = 0.0
        total_tokens = 0
        for exe in executions:
            if exe.state != StepExecutionState.COMPLETED:
                continue
            output = exe.output or {}
            if "cost_usd" not in output:
                continue
            info = instance_info.get(exe.instance_id)
            if info is None:
                instance = await self.repositories.instances.get(exe.instance_id)
                if instance is None:
                    continue
                info = (instance.workflow_id, bool((instance.context or {}).get("dry_run")))
                instance_info[exe.instance_id] = info
            wf, dry = info
            # Dry runs aren't representative of a real run's cost (external
            # tools are stubbed) — keep them out of the pre-run estimate.
            if wf != workflow_id or dry:
                continue
            instances_seen.add(exe.instance_id)
            total_cost += float(output.get("cost_usd", 0.0))
            total_tokens += int((output.get("usage") or {}).get("total_tokens", 0))
        return WorkflowRunStats(
            run_count=len(instances_seen),
            total_cost_usd=round(total_cost, 6),
            total_tokens=total_tokens,
        )

    async def by_model(
        self, since: datetime | None = None, org_id: str | None = None
    ) -> list[CostRow]:
        # Spend view: dry-run tokens are real Bedrock spend, so include them.
        return _group(
            [
                (str(output.get("model", "<unknown>")), output)
                for _, output, _dry in await self._sample(since, org_id)
            ]
        )

    async def by_day(
        self, since: datetime | None = None, org_id: str | None = None
    ) -> list[CostRow]:
        # Use the started_at of the step execution; for week 8 we approximate
        # by re-fetching in a second pass. Keep this O(N) — listed step
        # executions already include started_at, so reuse rather than
        # re-querying.
        executions = await self.repositories.steps.list_recent(
            limit=self.sample_limit, since=since, org_id=org_id
        )
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for exe in executions:
            if exe.state != StepExecutionState.COMPLETED:
                continue
            if not exe.started_at or not exe.output or "cost_usd" not in exe.output:
                continue
            day = exe.started_at.date().isoformat()
            groups[day].append(exe.output)
        rows: list[CostRow] = []
        for day, outputs in groups.items():
            total_cost = sum(float(o.get("cost_usd", 0.0)) for o in outputs)
            total_tokens = sum(int((o.get("usage") or {}).get("total_tokens", 0)) for o in outputs)
            rows.append(
                CostRow(
                    key=day,
                    total_cost_usd=round(total_cost, 6),
                    total_tokens=total_tokens,
                    step_count=len(outputs),
                )
            )
        rows.sort(key=lambda r: r.key, reverse=True)
        return rows


def _group(keyed: list[tuple[str, dict[str, Any]]]) -> list[CostRow]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, output in keyed:
        groups[key].append(output)
    rows: list[CostRow] = []
    for key, outputs in groups.items():
        total_cost = sum(float(o.get("cost_usd", 0.0)) for o in outputs)
        total_tokens = sum(int((o.get("usage") or {}).get("total_tokens", 0)) for o in outputs)
        rows.append(
            CostRow(
                key=key,
                total_cost_usd=round(total_cost, 6),
                total_tokens=total_tokens,
                step_count=len(outputs),
            )
        )
    rows.sort(key=lambda r: r.total_cost_usd, reverse=True)
    return rows
