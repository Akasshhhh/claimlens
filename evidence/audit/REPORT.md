# ClaimLens evidence audit

## Current scope

This is an offline, read-only audit of the corrected final evidence. It does
not start a provider, model, API server, Docker build, database, or Langfuse
request. The executable checker is `evidence/audit/repository_audit.py`; the
summary/comparison/manifest generator is `evidence/audit/regenerate_evidence.py`.

The four current final artifacts are:

```text
artifacts/v1-development-corrected-final/
artifacts/v2-development-corrected-final/
artifacts/v1-check-corrected-final/
artifacts/v2-check-corrected-final/
```

Historical frozen-tag, failed, and exploratory artifacts remain preserved and
are excluded from the current comparison. `evidence/MANIFEST.json` is a
selected-evidence manifest, not a full repository-tree hash.

## Current findings

| Area | Status | Evidence |
|---|---|---|
| Four final artifact sets | **VERIFIED** | Each has complete metadata, outcomes, predictions, report, exchanges, trace receipts, and one terminal outcome per scheduled answer. |
| Corrected v1/v2 provenance | **VERIFIED** | Both development and both check runs use source `a0020d06…`; prompt hashes differ only between v1/v2. Historical frozen-tag runs remain separate. |
| Development comparison | **VERIFIED with limitations** | Same corrected source/settings and prompt-only model-facing change; observed joint-F1 delta `+0.049480`, bootstrap estimate `+0.068996`, 95% CI `[-0.086781,+0.220426]`. |
| Advice/instruction policy | **VERIFIED for saved corrected runs** | Both corrected development reports have zero unmatched accepted prediction IDs; offline adversarial tests cover pure advice, opinions, instructions, injection, and factual clauses. Historical canary failures are preserved. |
| Check-split isolation | **VERIFIED** | Check reports retain predictions/errors and keep all gold-derived metrics/IDs null. |
| Retry/concurrency/validation | **VERIFIED offline** | Tests prove genuine overlap, max active attempts exactly 3, 429 retry, non-retryable auth/malformed output, gold isolation, bounds, IDs, quotes, Unicode, overlap, and reason normalization. |
| PostgreSQL/API integration | **VERIFIED in integration tests** | Actual FastAPI app with Docker PostgreSQL passed host and container integration suites; constraints, persistence, joined retrieval, sanitization, UUIDs, empty/error rows, and match fields are covered. |
| Langfuse local receipts/verifier | **VERIFIED** | Corrected runs have non-empty local receipts and no verifier-error file; per-answer IDs and explicit UTC windows are used. |
| Langfuse remote reviewer evidence | **INCOMPLETE** | Full remote retrieval of all 100 corrected traces and full sanitized payload exports were not retained. |
| Live API process/deployment | **INCOMPLETE/UNVERIFIED** | TestClient integration and Docker image build passed; `make run` was not retained as a live process smoke, and no hosted deployment is required. |
| Manifest/evidence freshness | **VERIFIED** | Current selected entries match actual bytes/SHA-256; current docs disclose selected scope. |

## Current run facts

| Run | Scheduled | OK | Errors | Predictions | Joint F1 | Error types |
|---|---:|---:|---:|---:|---:|---|
| v1 development | 40 | 22 | 18 | 201 | 0.665511 | 18 provider timeouts |
| v2 development | 40 | 26 | 14 | 231 | 0.714992 | 13 provider timeouts; 1 invalid output |
| v1 check | 10 | 5 | 5 | 44 | null | 5 provider timeouts |
| v2 check | 10 | 9 | 1 | 83 | null | 1 provider timeout |

Check quality is intentionally null, not zero. Exact answer IDs/messages are in
`evidence/final-run-summary.json` and each artifact's `outcomes.json`.

## Tests and receipts

```text
make test                         -> 102 passed, 1 warning
make verify                       -> exit 0; corrected metrics match
API/PostgreSQL integration         -> 24 passed, 1 warning
focused offline gate               -> 100 passed, 1 warning
Docker build                       -> passed
Docker container tests             -> 97 passed, 5 skipped without DB env
Docker DB/API integration          -> 10 passed, 1 warning
```

Receipts:

- `evidence/pytest-output.txt`
- `evidence/verify-output.txt`
- `evidence/final-run-summary.json`
- `evidence/submission/final-provenance.json`
- `evidence/MANIFEST.json`
- `evidence/audit/current-audit.json`

## Langfuse limitation

The verifier `timedelta` failure from the historical v2 development artifact
is not present in the corrected final artifacts. The corrected verifier catches
per-trace failures, keeps completed provider outcomes completed, uses the
per-answer trace ID, and records sanitized status/count/name/digest summaries.
The repository does not claim that local receipts are full remote reviewer
exports. The read-only auditor can be run with `--offline`, or without that
flag when an explicit remote audit is authorized.

## Preserved historical evidence

Old final directories, failed runs, old v1/v2 comparisons, old injection
canaries, and the first corrected v1 attempt remain intact. They are not used
to inflate or replace the corrected final metrics.
