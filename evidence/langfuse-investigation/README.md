# Langfuse investigation

## Current result

The historical v2 development artifact recorded this verifier failure:

```text
artifacts/v2-development-final/trace_verification_error.json
NameError: name 'timedelta' is not defined
```

The source now centralizes the `timedelta`-based fixed UTC run-window
construction in `claimlens.tracing.observation_window`. The verifier uses the
per-answer `trace_ids` from each saved `run.json`; it never uses the experiment
run UUID as a Langfuse `traceId`. Verification errors are recorded per trace
and do not alter provider outcomes, evaluation metrics, or the completed run
status.

## Evidence categories

| Category | Location | Meaning |
| --- | --- | --- |
| Locally recorded receipts | `artifacts/{v1-development-final,v2-development-final,v1-check-final,v2-check-final}/trace_verification.json` and `trace_exports.json` | Existing sanitized files written by the live runs; they are not re-claimed as current remote observations. |
| Current remote audit | `evidence/langfuse-investigation/audit-report.json` | Sanitized read-only observations queried by the audit script using all 100 saved per-answer trace IDs and each run's fixed window. |
| Full exports | None | Deliberately not collected; raw observations, prompts, model outputs, score payloads, and credentials are not exported. |

The current audit had credentials and connectivity: authenticated health was
HTTP 200 (`4.64.0`). It queried all 100 IDs without starting a provider or
model run. The endpoint returned 49 verified traces, 39 HTTP 429 responses, and
12 transport timeouts at the one-second per-request budget. Those exact
sanitized statuses, body digests, windows, and blockers are in
`audit-report.json`; no claim is made that the 51 unsuccessful queries are
missing from Langfuse.

The older baseline diagnosis remains in `evidence/trace-diagnosis.json`. Its
selected trace was remotely observed with four observations in its historical
run window, while its current-window query and the experiment UUID query were
empty. That evidence supports the identifier/window mismatch diagnosis, not a
provider correctness claim.

## Read-only commands

Health check (does not print either credential value):

```sh
set -a; . ./.env; set +a
curl -sS --user "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  -w '\nHTTP_STATUS=%{http_code}\n' \
  "$LANGFUSE_BASE_URL/api/public/health"
```

Audit all four saved final runs (no provider/model call):

```sh
.venv/bin/python evidence/langfuse-investigation/audit.py --spacing 0 --timeout 1
```

The script falls back between system `curl` and Python HTTP, records only
counts/names/statuses/digests, and records blockers when credentials or
connectivity are unavailable.

## Offline checks

```sh
.venv/bin/python -m pytest -q tests/langfuse tests/reliability/test_tracing.py \
  tests/harness/test_reports_comparison.py tests/audit/test_repository_audit.py
# 17 passed
.venv/bin/python -m compileall -q claimlens/tracing.py runner/experiment.py \
  evidence/langfuse-investigation/audit.py tests/langfuse/test_verifier.py
# full repository suite: 99 passed, 1 warning
```

The tests cover UTC run-window and per-answer identifier construction,
per-trace verifier error isolation, sanitized response summaries, offline
audit blockers, and existing check-score redaction. No provider experiment was
started for this investigation.
