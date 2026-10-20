# ClaimLens handoff

## Reviewer entry point

The current corrected evidence is summarized in:

- `evidence/final-run-summary.json`
- `evidence/v1-v2-final-comparison.json`
- `evidence/submission/final-provenance.json`
- `evidence/audit/current-audit.json`
- `evidence/MANIFEST.json`
- `README.md`

Only these four directories feed the current final summary and comparison:

```text
artifacts/v1-development-corrected-final/
artifacts/v2-development-corrected-final/
artifacts/v1-check-corrected-final/
artifacts/v2-check-corrected-final/
```

Every other `artifacts/*` directory is retained historical or exploratory
evidence. It is not deleted and is not silently substituted into the final
comparison.

## Current corrected final set

All four current final runs use source revision
`a0020d06e0d71fbe83fd43236cdec3b33983d8b6`. The corrected v1 and v2
development runs share the dataset, evaluator, provider, model, timeout,
retry, turn, and concurrency settings; the intended model-facing difference is
the v2 advice/instruction policy.

| Run | Run ID | Artifact | Result | Error rows |
|---|---|---|---|---|
| v1 development | `1d30770a-933b-4982-83be-875d24c02682` | `artifacts/v1-development-corrected-final/` | 40 scheduled, 22 ok, 201 predictions, joint F1 `0.665511` | 18 `provider_timeout` |
| v2 development | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `artifacts/v2-development-corrected-final/` | 40 scheduled, 26 ok, 231 predictions, joint F1 `0.714992` | 13 `provider_timeout`; A09 `invalid_output` |
| v1 check | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `artifacts/v1-check-corrected-final/` | 10 scheduled, 5 ok, 44 predictions, quality `null` | A43/A44/A46/A48/A50 `provider_timeout` |
| v2 check | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `artifacts/v2-check-corrected-final/` | 10 scheduled, 9 ok, 83 predictions, quality `null` | A46 `provider_timeout` |

The current audit reports all four artifact sets complete, with one terminal
outcome per scheduled answer, non-empty local trace receipts, no current
`trace_verification_error.json`, and no selected-manifest hash mismatch.

## Exact setup and operation commands

These are the reproducibility instructions for a clean checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
```

The supplied CSVs must be present in the repository root or selected through
`CLAIMLENS_DATA_DIR`. Credentials remain in the ignored `.env`.

Offline commands with retained current receipts are:

```bash
make test
make verify
make hash
PYTHONPATH=. .venv/bin/python evidence/audit/repository_audit.py
PYTHONPATH=. .venv/bin/python evidence/langfuse-investigation/audit.py \
  --offline --output evidence/langfuse-investigation/audit-report.json
```

Recorded results:

- `make test`: **VERIFIED**, 102 passed and 1 warning; receipt
  `evidence/pytest-output.txt`.
- `make verify`: **VERIFIED**, exit 0 and both corrected development metric
  objects matched; receipt `evidence/verify-output.txt`.
- PostgreSQL/API integration: **VERIFIED**, 24 passed and 1 warning.
- Focused agent/reliability/Langfuse/API/integration gate: **VERIFIED**, 100
  passed and 1 warning.
- Docker build: **VERIFIED**, passed.
- Docker container test suite without `DATABASE_URL`: **VERIFIED**, 97 passed
  and 5 skipped.
- Docker DB/API integration through `host.docker.internal`: **VERIFIED**, 10
  passed and 1 warning.

The exact PostgreSQL/API test command retained in the earlier handoff receipt
was:

```bash
.venv/bin/pytest -q tests/integration/test_api_postgres.py \
  tests/api/test_api.py tests/api/test_app.py
```

The API operation and local container commands are:

```bash
createdb claimlens
set -a; . ./.env; set +a
make run

docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

The Docker build/container test receipts are retained. A persistent live
`make run` process smoke is **INCOMPLETE**: the direct no-credentials Uvicorn
smoke below is not a configured `make run` process smoke. Hosted deployment is
**UNVERIFIED** and is not required by the assessment.

A separate no-credentials process smoke is **VERIFIED only as a
validation/configuration smoke**. The exact command was:

```bash
env OPENAI_API_KEY= OPENAI_MODEL= DATABASE_URL= \
  LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= LANGFUSE_BASE_URL= \
  LANGFUSE_HOST= CLAIMLENS_DATA_DIR=. CLAIMLENS_ARTIFACT_DIR=artifacts \
  PYTHONPATH=. .venv/bin/python -m uvicorn claimlens.api.app:app \
  --host 127.0.0.1 --port 8765
```

`evidence/run-smoke.txt` records clean startup and all three endpoint paths:
invalid `/classify` returned 422, while the two evaluation-run paths returned
configuration blockers because `DATABASE_URL` was empty. No valid classify
payload was sent and no provider/model/Langfuse request was initiated. This is
not a configured `make run` smoke.

## Corrected metrics and error accounting

The development split has `G = 376` gold claim sentences. The corrected
headline metrics are:

| Metric | v1 | v2 |
|---|---:|---:|
| Predictions (`P`) | 201 | 231 |
| Matched spans (`M`) | 201 | 231 |
| Correct labels (`C`) | 192 | 217 |
| Extraction precision | 1.000000 | 1.000000 |
| Extraction recall | 0.534574 | 0.614362 |
| Extraction F1 | 0.696707 | 0.761120 |
| Joint precision | 0.955224 | 0.939394 |
| Joint recall / correct-label coverage (`C/G`) | 0.510638 | 0.577128 |
| Joint F1 | 0.665511 | 0.714992 |
| Macro label F1 | 0.665583 | 0.714444 |

Observed joint-F1 delta is `+0.049480`. The paired bootstrap estimate is
`+0.068996` with a 95% CI of `[-0.086781, +0.220426]` from 2,000 paired
resamples over 40 answers with seed `0`; zero is inside the interval. The
comparison is not statistically conclusive; all claim transitions are retained
in `evidence/v1-v2-final-comparison.json`.

Corrected answer-level errors are:

- v1 development: 18 `provider_timeout` outcomes for A01, A02, A04, A06,
  A07, A09, A11, A12, A17, A22, A28, A30, A32, A33, A34, A37, A39, A40.
- v2 development: 13 `provider_timeout` outcomes for A03, A04, A06, A08,
  A11, A17, A29, A31, A33, A34, A37, A39, A40; A09 was an
  `invalid_output` (`claim fields are incomplete or unknown`).
- v1 check: five `provider_timeout` outcomes for A43, A44, A46, A48, A50.
- v2 check: one `provider_timeout` outcome for A46.

Timeouts remain answer-level errors. They produce no accepted predictions and
count every gold claim for the affected development answer as a missed `G`
claim. They are not converted into `unknown` labels. The 15-second
per-provider-request limit remains unchanged from the assessment specification;
the runner still allows at most two attempts per turn and three model turns.
This documentation pass changed no timeout, retry, artifact, or code behavior.

These provider timeouts are not rate-limit responses and do not establish quota
exhaustion. The separate HTTP 429 responses in the Langfuse audit are
post-run audit responses, not the provider answer errors. The live provider
runs used a non-premium account; that account-tier limitation is documented,
but the retained evidence does not prove it caused any timeout. The cause of
the differing success rates is **UNVERIFIED**.

The check reports retain accepted predictions, errors, timings, and terminal
answer outcomes. All gold-derived fields remain `null`: gold claims, matched
spans, correct labels, extraction/joint precision/recall/F1, coverage,
per-label metrics, macro F1, confusion matrices, and missed/unmatched/wrong
label IDs. Check quality is intentionally `null`, not zero; the hiring team
holds the labels.

## Historical advice and injection evidence

The historical frozen v1 development analysis (run
`8cb56eec-6c54-4eab-aba2-317bee75d451`, artifact
`artifacts/v1-development-final-turn-gated/`) recorded 605 predictions and 261
unmatched predictions: 254 advice/instruction-only spans, 3 explicit
meta-instruction markers, 2 factual-looking supported sentences, and 2
unsupported/unresolved sentences. The historical v1 and v2 final injection
canaries both failed for A07, A18, and A32 because the marker sentence was
extracted; `override_suspected` was false and not all labels were `supported`.
The evidence is retained in:

- `evidence/analysis/v1-development-final-turn-gated-analysis.json`
- `evidence/v1-final-injection-canary.json`
- `evidence/v2-final-injection-canary.json`

In the corrected development artifacts, every accepted prediction is an exact
gold span (`unmatched_prediction_ids` is empty in both reports), and the saved
corrected advice-only diagnostic count is zero. Offline tests cover pure advice,
opinions, instructions, prompt injection, imperative-plus-fact sentences,
Unicode spans, and exact output validation. These corrected saved-run/offline
results do not rewrite the historical failed canaries.

The retained evidence supports that the intended v2 prompt policy came from
the saved v1 development analysis, that no private check labels or check-quality
metrics were loaded or inspected, and that no direct evidence was found that an
individual filter token was added because of a check-split output. Since the
answer inputs were visible and no external decision log exists, the repository
cannot independently confirm that no undocumented decision considered visible
check text. It does not claim a blind benchmark or claim that no check-informed
tuning of any kind occurred.

## Langfuse evidence

Each corrected final artifact has one local trace receipt per scheduled answer,
generation/tool events, answer diagnostics, and development score events where
applicable. The corrected verifier uses each per-answer trace ID and an
explicit UTC run window. The historical v2 verifier `NameError` is preserved
in `artifacts/v2-development-final/trace_verification_error.json`; the first
corrected v1 attempt also preserved an `AttributeError` trace-window failure,
`'str' object has no attribute 'isoformat'`, in
`artifacts/v1-development-corrected/trace_verification_error.json`.

The later retained sanitized audit
(`evidence/langfuse-investigation/audit-report.json`, generated at
2026-10-19T22:46:50Z) initially queried all 100 corrected trace IDs and
recorded 86 non-empty observations plus 14 HTTP 429 responses. The subsequent
serial re-query retained in `evidence/langfuse/langfuse-evidence.md` and
`evidence/langfuse/langfuse-summary.json` recovered all 14: final remote
confirmation is v1 development 40/40, v2 development 40/40, v1 check 10/10,
and v2 check 10/10, with no remaining inaccessible traces. The 429s were
Langfuse audit responses, not provider answer timeouts or quota evidence.

The current reviewer export is sanitized observation metadata, not a full raw
payload export. It intentionally omits full prompts/answers, model outputs,
tool payloads, score payloads, credentials, and environment values.

The exact unfinished Langfuse work is **INCOMPLETE**: retain full
reviewer-facing remote payload exports for all 100 corrected traces. The
current sanitized export contains observation metadata and selected truncated
text, but not full prompts/answers, model/tool payloads, score payloads,
credentials, or raw response bodies.

## Sequence and deviations

The following list is chronological by saved `started_at` (UTC); all entries
are from 2026-10-19. It includes the diagnostic runs that drove bounded-runtime
changes, the early freeze
tags/runs, the first corrected attempt, and the four current final runs. A
superseded artifact remains evidence but is excluded from the current final
comparison.

In this table, **VERIFIED** describes the saved artifact and its local
accounting; it does not make private check quality available.

The early `v1-frozen` and `v2-frozen` tags both point to `a9ce77e`. The later
final freeze tags point to `v1-final-frozen` = `9de1a5ec...` and
`v2-final-frozen` = `8293d189...`.

| UTC start | Run ID | Artifact path | Reason | Status |
|---|---|---|---|---|
| 12:23 | `a5a7feab-02df-4951-8c53-93184e347bb8` | `artifacts/smoke-v1-A01/` | Initial one-answer smoke. | **DIAGNOSTIC/SUPERSEDED** — 1/1 ok, no predictions. |
| 12:30 | `0cd3dd45-2a1c-41bb-9962-9eba33358ac6` | `artifacts/v1-development/` | Initial 40-answer v1 run. | **FAILED/SUPERSEDED** — 0/40 ok; all 40 errored. |
| 12:36 | `23b3e271-2677-4969-bf53-3790665c6b1a` | `artifacts/v1-escalation-3/` | Escalation after full-split provider timeouts. | **DIAGNOSTIC/SUPERSEDED** — 2/3 ok; no predictions. |
| 12:41 | `30beb6e6-a7e3-4a2b-8a15-9f682b3cd076` | `artifacts/v1-development-rerun/` | Rerun with a verified runner and trace throttling. | **FAILED/SUPERSEDED** — 0/40 ok; all 40 errored. |
| 12:48 | `c2a4f339-e77c-463f-b1b0-6dd8a4b52f3c` | `artifacts/v1-smoke-compact/` | Provider payload compaction after extraction timeout/empty-output diagnosis. | **DIAGNOSTIC/SUPERSEDED** — 1/1 ok. |
| 12:50 | `10c4da2b-8e80-42e7-aa06-a203d213d343` | `artifacts/v2-smoke/` | Targeted v2 extraction-obligation prompt smoke. | **DIAGNOSTIC/SUPERSEDED** — 1/1 ok. |
| 12:51 | `916ec3da-3b4a-49e2-9fd7-1efa4386ca0e` | `artifacts/v2-development/` | Full v2 run after the targeted extraction-obligation prompt change. | **FAILED/SUPERSEDED** — 0/40 ok; all 40 errored. |
| 12:57 | `200802cf-9bc8-4186-ae9a-9a95533fbf8d` | `artifacts/v1-check/` | First frozen final-check attempt. | **HISTORICAL/SUPERSEDED** — 6/10 ok; 4 errors; no predictions. |
| 12:58 | `ea08c511-28fb-4f69-a8ac-bc963417673b` | `artifacts/v2-check/` | First frozen final-check attempt. | **HISTORICAL/SUPERSEDED** — 2/10 ok; 8 errors; no predictions. |
| 13:44 | `d6aaa8f2-f0e1-4fb3-9a5d-c7e7aa8e7392` | `artifacts/diagnosis-smoke-v1/` | Diagnostic smoke after minimal reasoning and output-token-cap fixes. | **FAILED/SUPERSEDED** — 0/3 ok. |
| 13:51 | `e3c80247-c72f-4b05-807b-5680f1879346` | `artifacts/diagnosis-smoke-v1-fixed/` | Quote localization and compact second-turn request fix. | **DIAGNOSTIC/SUPERSEDED** — 2/3 ok; 32 predictions. |
| 13:54 | `1e43c88f-d5b9-4921-926a-275014068068` | `artifacts/diagnosis-smoke-v1-fixed-2500/` | Lowered completion budget to 2500 after an A02 second-turn timeout. | **DIAGNOSTIC/SUPERSEDED** — 2/3 ok; 32 predictions. |
| 13:55 | `5a84f75a-55d5-4c60-b3a9-3faa9619a81c` | `artifacts/diagnosis-smoke-A02-serial/` | Isolated A02 after a concurrent second-turn timeout. | **FAILED/SUPERSEDED** — 0/1 ok. |
| 13:59 | `c9d54086-7f56-42e2-8ad7-2aa134ac6f8f` | `artifacts/diagnosis-smoke-v1-valid/` | Reason normalization after an A02 malformed explanation. | **DIAGNOSTIC/SUPERSEDED** — 3/3 ok; 49 predictions. |
| 14:00 | `24f3bc72-7e77-4924-9d27-6dca044b6c1c` | `artifacts/v1-development-final/` | Root-cause fixes: minimal reasoning, 2500-token cap, compact tool messages, Python quote localization, and bounded reasons. | **FAILED/SUPERSEDED** — 0/40 ok. |
| 14:09 | `4bab7a3c-7093-49a1-b151-1d62d75a6f7f` | `artifacts/diagnosis-smoke-v1-valid-2000/` | Lowered completion budget to 2000 after full-split second-turn timeouts. | **DIAGNOSTIC/SUPERSEDED** — 3/3 ok; 49 predictions. |
| 14:10 | `b5a0ac92-e856-45b2-8ef0-f9466e12460f` | `artifacts/v1-development-final-2000/` | Second bounded-call fix: 2000-token cap after 2500-token full-split timeouts. | **FAILED/SUPERSEDED** — 0/40 ok; old trace timeout receipt retained. |
| 14:22 | `e2977432-f616-4be5-bce7-6d4f324ba662` | `artifacts/diagnosis-smoke-v1-turn-gated/` | Serialized only post-tool completion turns after the full-split burst diagnosis. | **DIAGNOSTIC/SUPERSEDED** — 3/3 ok; 49 predictions. |
| 14:24 | `8cb56eec-6c54-4eab-aba2-317bee75d451` | `artifacts/v1-development-final-turn-gated/` | v1 final frozen run after post-tool turn serialization. | **HISTORICAL/SUPERSEDED** — 37/40 ok; 605 predictions; tag `v1-final-frozen`. |
| 17:18 | `5a64e5ff-d1e4-4e49-8175-e2c3617c26cb` | `artifacts/v2-development-final/` | v2 final frozen run after v1 analysis; removed pure advice/instruction/opinion-only extraction. | **HISTORICAL/SUPERSEDED** — 37/40 ok; 610 predictions; tag `v2-final-frozen`; old verifier error retained. |
| 17:36 | `93af5381-5900-43f5-8543-29e32e1ee64f` | `artifacts/v1-check-final/` | Frozen v1 check after v1 analysis and verifier repair. | **HISTORICAL/SUPERSEDED** — 9/10 ok; 143 predictions; quality `null`. |
| 17:41 | `a58a7b95-2784-4184-abd5-2161a9386028` | `artifacts/v2-check-final/` | Frozen v2 check after the advice-policy change. | **HISTORICAL/SUPERSEDED** — 7/10 ok; 117 predictions; quality `null`. |
| 21:16 | `ec01ca09-4d16-4261-916f-0902d16b2e89` | `artifacts/v1-development-corrected/` | First corrected development attempt after shared validation/adversarial extraction fixes. | **INCOMPLETE/SUPERSEDED** — 18/40 ok; 162 predictions; trace-window `AttributeError: 'str' object has no attribute 'isoformat'`. |
| 21:34 | `1d30770a-933b-4982-83be-875d24c02682` | `artifacts/v1-development-corrected-final/` | Rerun after the trace-window string fix; prior corrected provider outputs preserved. | **CURRENT FINAL/VERIFIED** — 22/40 ok; 201 predictions; local receipts complete. |
| 21:52 | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `artifacts/v2-development-corrected-final/` | Corrected v2 rerun after shared validation/adversarial extraction fixes. | **CURRENT FINAL/VERIFIED** — 26/40 ok; 231 predictions; local receipts complete. |
| 22:09 | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `artifacts/v1-check-corrected-final/` | Corrected v1 check; historical check preserved. | **CURRENT FINAL/VERIFIED** — 5/10 ok; 44 predictions; quality `null`. |
| 22:14 | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `artifacts/v2-check-corrected-final/` | Corrected v2 check; historical check preserved. | **CURRENT FINAL/VERIFIED** — 9/10 ok; 83 predictions; quality `null`. |

### Why the pipeline changed after the first checks

The required sequence was attempted, but the first full runs and early checks
were preserved because provider errors and bounded-execution behavior needed
diagnosis before a meaningful comparison. The first check outputs were
error-heavy: v1 was 6/10 with no predictions and v2 was 2/10 with no
predictions. The saved v1 development analysis then found substantial
advice/instruction over-extraction and the historical injection canaries
failed. Those first final-check outputs were also not safe to promote as the
submission while shared validation and trace verification still had known
defects.

The pipeline therefore hardened shared claim validation, advice/instruction
filtering, gold isolation, and trace verification, then reran both development
versions and both checks from one post-fix source revision. The first corrected
v1 attempt (`ec01ca09-4d16-4261-916f-0902d16b2e89`) produced provider outputs
but its trace-window verifier failed with `AttributeError: 'str' object has no
attribute 'isoformat'`. The old v2 development artifact separately retains
`NameError: name 'timedelta' is not defined`. The string/window fix was applied
before the four current final runs; completed provider outcomes were not
rewritten because trace verification is post-run and isolated from scoring.

Both earlier frozen development runs were 37/40 ok. The corrected v1 and v2
development runs were 22/40 and 26/40 ok. The cause of this run-to-run
variation is unproven. The 15-second request limit and retry policy were not
changed to explain it. The check reports contain no private quality labels, so
the correction was not a check-score optimization.

## Status checklist

| Requirement | Status | Evidence / remaining action |
|---|---|---|
| Pinned runtime and lockfile | **VERIFIED** | `.python-version`, `requirements.lock`, `Dockerfile` |
| Exact-span evaluator and saved-run rescoring | **VERIFIED** | `make verify`, `evidence/verify-output.txt` |
| Corrected four artifact sets and terminal accounting | **VERIFIED** | `evidence/audit/current-audit.json`; all four current directories |
| Check-score nulling | **VERIFIED** | Current check `report.json` files keep all gold-derived fields null |
| Advice/instruction policy | **VERIFIED for saved corrected runs/offline tests** | Zero unmatched accepted prediction IDs; historical canaries remain failed evidence |
| Concurrency ceiling and selective retries | **VERIFIED offline** | Reliability tests; max active attempts exactly 3 and 429 retry coverage |
| Gold isolation and strict claim validation | **VERIFIED offline** | Agent tests cover payload isolation, IDs, bounds, quotes, Unicode, overlap, and reasons |
| PostgreSQL constraints/persistence | **VERIFIED in integration tests** | Host and container API/DB suites passed |
| Langfuse local receipts/verifier | **VERIFIED** | Current receipts are non-empty and current runs have no verifier-error file |
| Langfuse remote confirmation | **VERIFIED for sanitized confirmation** | The later serial re-query receipt reports all 100 current traces confirmed; no remaining inaccessible traces. Full payload exports remain incomplete |
| Langfuse full remote payload exports | **INCOMPLETE** | Sanitized observation metadata is retained, but full raw reviewer-facing payloads are not |
| Live API process smoke | **INCOMPLETE** | No configured `make run` smoke receipt; no-credentials Uvicorn blocker smoke is retained |
| Blind check quality | **UNVERIFIED** | Check labels are held by the hiring team |
| Hosted deployment | **UNVERIFIED** | Not required by the assessment |
| Cause of timeout/success-rate variation | **UNVERIFIED** | Non-premium account is a limitation, not a proven cause |

## Disclosure

OmniRush.ai and delegated coding agents assisted with implementation and test
writing. Credentials remain only in the ignored `.env`; local CSV inputs remain
ignored and only their hashes are recorded. This documentation pass changed
only `README.md` and `HANDOFF.md`, did not run a model/provider call, and did
not commit. Historical and superseded evidence is preserved.
