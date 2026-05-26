#!/usr/bin/env bash
# Fire every PDF under examples/invoice_extraction/fixtures/ through the
# workflow. Each run sends 3 notification emails to the operator address,
# so think before pointing this at the full 1000-PDF sample set.
#
# Usage (from repo root):
#   examples/invoice_extraction/scripts/run_batch.sh
#
# Or point at the external 1000-PDF sample set:
#   FIXTURES_DIR=/home/ubuntu/Documents/intelligent-workflow-engine/sample-invoices/1000-pdf-invoice-samples \
#     examples/invoice_extraction/scripts/run_batch.sh
#
# Same env vars as run_one.sh.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../../.." && pwd)"
fixtures_dir="${FIXTURES_DIR:-${repo_root}/examples/invoice_extraction/fixtures}"
script_dir="${repo_root}/examples/invoice_extraction/scripts"

failures=0
total=0
for pdf in "${fixtures_dir}"/*.pdf; do
  total=$((total + 1))
  echo
  echo "==> $(basename "${pdf}")"
  if ! "${script_dir}/run_one.sh" "${pdf}"; then
    failures=$((failures + 1))
  fi
done

echo
echo "================================================================"
echo "  ${total} invoices fired; ${failures} failed."
echo "================================================================"
exit "${failures}"
