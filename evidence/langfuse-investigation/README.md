# Langfuse investigation

## Current state

The historical v2 development artifact recorded a verifier `NameError` because
`timedelta` was missing. That source bug is fixed. The corrected final runs use
per-answer trace IDs and explicit UTC windows, isolate per-trace verifier
failures, and have no `trace_verification_error.json`.

Current local receipts are in:

```text
artifacts/v1-development-corrected-final/trace_verification.json
artifacts/v2-development-corrected-final/trace_verification.json
artifacts/v1-check-corrected-final/trace_verification.json
artifacts/v2-check-corrected-final/trace_verification.json
```

They are sanitized local summaries containing trace IDs, observation counts,
names, local event counts and score summaries. They are not full remote
Langfuse exports.

## Evidence categories

| Category | Location | Meaning |
|---|---|---|
| Corrected local receipts | `artifacts/*-corrected-final/trace_verification.json` and `trace_exports.json` | Saved per-answer receipt summaries from the corrected runs. |
| Read-only audit | `audit-report.json` | Sanitized status/count/name/digest results; no raw payloads or credentials. |
| Historical diagnosis | `evidence/trace-diagnosis.json` and historical `report.json` | Earlier selected-trace/window-mismatch investigation, retained as historical evidence. |
| Full exports | None | Deliberately not collected; prompts, model outputs, tool payloads, score payloads, and secrets are not exported. |

The current offline audit command is safe and makes no network or model call:

```bash
PYTHONPATH=. .venv/bin/python evidence/langfuse-investigation/audit.py \
  --offline --output evidence/langfuse-investigation/audit-report.json
```

When explicitly run without `--offline`, the script uses all saved trace IDs and
the run's fixed time window, but retains only sanitized summaries. The current
repository does not claim that all 100 corrected traces were remotely retrieved;
full reviewer-facing remote evidence remains incomplete.

## Offline checks

```bash
.venv/bin/pytest -q tests/langfuse tests/reliability/test_tracing.py \
  tests/harness/test_reports_comparison.py tests/audit/test_repository_audit.py
.venv/bin/python -m compileall -q claimlens/tracing.py runner/experiment.py \
  evidence/langfuse-investigation/audit.py tests/langfuse/test_verifier.py
```

The tests cover UTC window and per-answer identifier construction, verifier error
isolation, sanitized response summaries, offline audit blockers, and check-score
redaction. No provider experiment is started by these commands.
