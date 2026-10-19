# ClaimLens evidence audit

## Current scope

Audit date: 2026-10-20.  This is an offline, read-only audit of the selected
evidence scope at committed `HEAD`
`4711df54bcf55ec1ef9ef50ae6cd43ec9cfbef02`.  It does not start a provider,
database, API server, Docker build, or Langfuse request.  The checker is
`evidence/audit/repository_audit.py`; the summary/comparison/manifest generator
is `evidence/audit/regenerate_evidence.py`.

The four designated artifacts are:

```text
artifacts/v1-development-final-turn-gated/
artifacts/v2-development-final/
artifacts/v1-check-final/
artifacts/v2-check-final/
```

All other artifact directories and older evidence files remain preserved as
historical or exploratory material.  They are not part of the final summary or
comparison.  `evidence/MANIFEST.json` is a selected-evidence manifest and
explicitly does not claim full repository-tree coverage.

## Status key

- **Complete**: the saved files and offline checks directly support the item.
- **Incomplete**: a saved inconsistency, error receipt, or unverified required
  runtime step remains.
- **Unverified**: the implementation or documentation exists, but the required
  environment/live check was not run or is not retained.

## Current findings

| Area | Status | Finding |
|---|---|---|
| Four final artifact directories | **Complete** | Each has `run.json`, `config.json`, `outcomes.json`, `predictions.json`, `report.json`, `timings.json`, `exchanges.jsonl`, `trace_verification.json`, and `trace_exports.json`. Each has one terminal outcome per scheduled answer, including errors and empty claims. |
| Development source freezes | **Complete** | v1 development records `9de1a5ec...` and matches `v1-final-frozen`; v2 development records `8293d189...` and matches `v2-final-frozen`; the revisions differ as intended. |
| Later check source | **Complete and disclosed** | Both final checks record `c76941fec...`, a later shared check-run revision. It is not presented as either frozen development source. |
| Development comparison | **Complete as saved evidence; scope limited** | The comparison holds datasets, model/provider settings, and scoring fixed and reports the actual +0.018963 joint-F1 delta. The two source tags also contain trace/runner/evidence changes in addition to the v2 prompt; this is disclosed and is not described as a byte-for-byte prompt-only Git diff. |
| Advice/instruction diagnosis | **Complete** | The saved v1 analysis reports 261 unmatched predictions, including 254 advice/instruction-only and 3 meta-instruction markers. The v2 bucket is retained as a heuristic diagnostic, not a gold metric. |
| Fixed/regressed provenance | **Complete** | `evidence/submission/final-provenance.json` separates 19 error-driven fixed IDs from 11 genuine fixed IDs, 14 error-driven regressed IDs from 5 genuine regressed IDs, and records the other five error-driven state changes. |
| Check-split isolation | **Complete** | Both check reports retain predictions and answer errors but keep gold claims, matches, correctness, quality metrics, and gold-derived ID lists null. |
| Trace/event diagnostics | **Complete locally; verifier process warning** | Local exchanges contain terminal accounting, `provider_attempt`, `answer_diagnostics`, generation, and tool events for all four runs. All saved receipt rows are non-empty. `v2-development-final/trace_verification_error.json` still records `NameError: name 'timedelta' is not defined`, so the audit exposes a separate incomplete verifier-process status without treating the saved receipt rows as missing. |
| Langfuse remote reviewability | **Incomplete** | The selected investigation retrieved one trace with root, generation, and tool observations. The repository retains receipt summaries for the runs, not full reviewer-facing payloads for all 100 remote traces. Query-window and identifier limitations are documented. |
| Manifest selected scope | **Complete** | The manifest has 223 selected file entries; every listed path exists and its recorded byte count and SHA-256 match. The generated commit is current `HEAD`. Current-vs-frozen prompt hashes are recorded separately. |
| Current v2 prompt provenance | **Complete and disclosed** | The current committed v2 prompt hash is `75971d63...`; final v2 artifacts record `74ea421c...`. The difference is a later committed imperative-wording clarification, not a rewritten final run. |
| API/PostgreSQL implementation | **Complete in offline/integration tests; live process unverified** | Routes, constraints, parameterized persistence, joined retrieval, and tests exist. The current `make test` run reports 99 passing tests; `make run` was not started. |
| Docker/local deployment | **Unverified** | `Dockerfile` and exact commands exist. The recorded Docker build was blocked by the stopped local daemon. No hosted deployment is required by the assessment. |

## Run facts

| Run | Scheduled | OK | Errors | Predictions | Error types |
|---|---:|---:|---:|---:|---|
| v1 development | 40 | 37 | 3 | 605 | A04/A22/A30 `provider_timeout` |
| v2 development | 40 | 37 | 3 | 610 | A25 empty/trimmed quote; A30/A34 exact-source-quote validation |
| v1 check | 10 | 9 | 1 | 143 | A49 exact-source-quote validation |
| v2 check | 10 | 7 | 3 | 117 | A41/A42/A43 `provider_timeout` |

The accepted-prediction decomposition is `A04 +18`, `A22 +17`, `A25 -16`,
`A34 -15`, and `A32 +1`, for `605 -> 610` (`+5`).  The exact run IDs, metric
objects, error messages, and source revisions are in
`evidence/final-run-summary.json`.

## Injection and trace limitations

The v1 and v2 final development canaries both have `passed: false`.  A07, A18,
and A32 each extracted the marker sentence.  They did not all label the answer
`supported`, and `override_suspected` is false, but marker extraction is still
a failed canary condition.

The local trace receipts prove that the run wrote and fetched non-empty
observation summaries.  They do not provide complete remote payloads for
reviewers.  `evidence/trace-diagnosis.json` contains one sanitized selected
trace; `evidence/langfuse-investigation/report.json` explains the HTTP 200
health check, authenticated observation query, stale/current window mismatch,
and the fact that an experiment UUID is not a trace ID.  No claim is made that
all remote traces were independently retrieved from this repository.

## Exact offline commands and results

```text
PYTHONPATH=. .venv/bin/python evidence/audit/regenerate_evidence.py
exit 0; offline; generated_for_commit=4711df54bcf55ec1ef9ef50ae6cd43ec9cfbef02;
manifest_entries=223; joint_f1_delta=0.018962725868582164

.venv/bin/python evidence/audit/repository_audit.py
exit 0; required_artifact_receipts=complete;
development_comparability=complete; selected manifest scope=complete;
prompt_provenance=current_source_differs_from_frozen_run;
trace_diagnostics=complete for saved receipts/events; verification_process_status=incomplete because of the saved v2 verification error file

make verify
exit 0; both saved development metric objects matched deterministic rescoring;
no provider call

make test
exit 0; 99 passed, 1 warning
```

The full captured command outputs are `evidence/pytest-output.txt` and
`evidence/verify-output.txt`; workspace-local absolute paths are redacted to
`<workspace>` for portability.  The warning is retained verbatim; this audit
did not modify source or tests to change it.

## Historical evidence retained

The following are intentionally not deleted:

- `evidence/run-summary.json`: earlier zero-prediction/timeout sequence;
- `evidence/v1-v2-comparison.json`: earlier comparison;
- `evidence/v1-injection-canary.json` and `evidence/v2-injection-canary.json`:
  earlier canary outputs;
- earlier exploratory/smoke artifact directories under `artifacts/`.

They are marked as superseded in `evidence/MANIFEST.json`.  Only the four final
directories listed above are current final evidence.

## Reproduction limits and missing requirements

The repository has a pinned runtime, lockfile, Dockerfile, `.env.example`,
exact setup/run/test/verify instructions, and local CSV hashes.  The following
remain incomplete or unverified: the API process smoke was not run, the Docker
daemon was unavailable for a build, the v2 trace-verification error file
remains, and no hosted deployment or full remote Langfuse export was
performed.  The final-check quality scores remain intentionally unavailable
because the assessment holds their labels privately.
