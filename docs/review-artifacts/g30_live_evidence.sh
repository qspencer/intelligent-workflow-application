#!/usr/bin/env bash
# G30 live evidence — what production did under the system layer.
#
# Needs the production box (service on 127.0.0.1:8001, Postgres db `workflow`),
# so a reviewer cannot re-run it; it ships so the queries behind every live
# figure in the guide are on the record. Counts and enum values only — no
# message content is read. Exit codes are taken before any pipe.
set -uo pipefail
export PGPASSWORD="${PGPASSWORD:-workflow}"
q() { psql -h localhost -U workflow -d workflow -At -v ON_ERROR_STOP=1 -c "$1"; }

echo "G30 live evidence — captured $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
echo "=== the deployed commit and the service ==="
echo "repo HEAD: $(git -C "$(dirname "$0")/../.." rev-parse --short HEAD)"
echo "service started: $(systemctl --user show workflow-be -p ActiveEnterTimestamp --value)"
echo
echo "=== /api/health (unauthenticated: counts only) ==="
curl -s http://127.0.0.1:8001/api/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('status:', d['status']); print('system_capabilities:', json.dumps(d.get('system_capabilities'), sort_keys=True))"
echo
# Phase 1 was deployed at the b3c387a restart; every real run since then ran
# under the layer. Dry runs excluded throughout.
P1="2026-10-29T13:33:39Z"
echo "=== real runs under the layer since phase 1 ($P1), by workflow ==="
q "select workflow_id || ': runs=' || count(*) || ' completed=' || count(*) filter (where state='completed') || ' failed=' || count(*) filter (where state='failed')
   from workflow_instances where created_at > '$P1' and coalesce(context->>'dry_run','false') <> 'true'
   group by workflow_id order by workflow_id;"
echo
echo "=== email-triage-apply: the one governed tool, used under the layer ==="
q "select 'apply steps run: ' || count(*) || '   label calls ok: ' || count(*) filter (where (s.output->'tool_calls'->0->>'result_ok')='true') || '   label calls failed: ' || count(*) filter (where (s.output->'tool_calls'->0->>'result_ok')='false')
   from step_executions s join workflow_instances i on i.id=s.instance_id
   where i.created_at > '$P1' and coalesce(i.context->>'dry_run','false') <> 'true' and s.step_id='apply';"
q "select 'largest agent-step output since phase 1: ' || coalesce(max((s.output->'usage'->>'output_tokens')::int)::text,'-') || ' tokens (cap 4096)'
   from step_executions s join workflow_instances i on i.id=s.instance_id
   where i.created_at > '$P1' and coalesce(i.context->>'dry_run','false') <> 'true' and s.output ? 'usage';"
echo
echo "=== memory distillation: the size the system cap now bounds ==="
q "select 'distillations recorded: ' || count(*) || '   largest output_tokens per observation: ' || max((detail->>'output_tokens')::int)
   from audit_log where action='memory_observed' and (detail->>'output_tokens') ~ '^[0-9]+$';"
echo "(per observation is the SUM of its distill calls, so each call is at most this)"
echo
echo "=== scheduled-health-report: did the moved log get written for real? ==="
L="$HOME/.local/share/workflow-platform/health/scheduled-health-report.log"
if [ -f "$L" ]; then echo "real log exists: $(wc -l < "$L") line(s)"; else echo "real log NOT YET WRITTEN at $L"; fi
echo
echo "=== every real DMARC file against the shipped layer ==="
cd "$(dirname "$0")/../../backend" && uv run python - <<'PY' 2>/dev/null
from pathlib import Path
from workflow_platform.security.capabilities import SHIPPED_SYSTEM_CAPABILITIES, load_system_capabilities, resolve_capabilities
caps = resolve_capabilities(load_system_capabilities(SHIPPED_SYSTEM_CAPABILITIES))
wp = Path.home() / ".local/share/workflow-platform/dmarc"
for label, root, mode in [("spool (read)", wp / "spool", "read"), ("extracted (read)", wp / "extracted", "read"),
                          ("extracted (write)", wp / "extracted", "write"),
                          ("viewer xml-files (write)", Path.home() / "Dev/dmarc-viewer/xml-files", "write")]:
    files = [p for p in root.rglob("*") if p.is_file()]
    denied = [p for p in files if not (caps.can_read(str(p)) if mode == "read" else caps.can_write(str(p)))]
    print(f"{label:26} files={len(files):4}  admitted={len(files) - len(denied):4}  denied={len(denied)}")
PY
