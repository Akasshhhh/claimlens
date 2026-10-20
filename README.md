# ClaimLens

ClaimLens extracts complete factual product sentences from long AI answers,
looks up catalogue facts through a real `get_brand_facts` tool call, validates
Unicode spans and labels in Python, and scores saved predictions
deterministically. It also provides bounded async execution, selective retries,
FastAPI/PostgreSQL persistence, Langfuse tracing, and reproducible evidence.

## Repository map

```text
answers.csv + brand_facts.csv -> loaders -> ClaimAgent
                                       |-> get_brand_facts
                                       |-> exact span/label validation
                                       `-> Langfuse observations

OpenAIProvider -> BoundedProvider -> AnswerOutcome -> evaluator/artifacts/DB
```

- `agent/`: tool loop, strict output validation, advice/instruction filtering,
  retries, turn limits, and answer-level errors.
- `providers/`: OpenAI SDK adapter and transport error mapping.
- `runner/`: bounded concurrent execution and immutable run artifacts.
- `harness/`: exact-span matching, extraction/joint metrics, comparisons, and
  injection canaries.
- `claimlens/api/`: FastAPI endpoints.
- `claimlens/db/`: PostgreSQL schema, repository, and artifact import.
- `evidence/`: selected manifest, audit, Langfuse, and submission evidence.

## Setup

Python `3.12.7` is pinned in `.python-version`; dependencies are pinned in
`requirements.lock`. For a clean checkout, the setup commands are:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
```

Place the supplied `answers.csv`, `brand_facts.csv`, and
`development_gold.csv` in the repository root, or set `CLAIMLENS_DATA_DIR`.
Their SHA-256 values are recorded in `evidence/MANIFEST.json`; the local CSVs
remain ignored and are not committed.

For live operation, set these values in the ignored `.env`:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini-2025-08-07
DATABASE_URL=postgresql://claimlens:claimlens@localhost:5432/claimlens
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

No model/provider call is needed for the tests, evaluator, saved-run
verification, or offline audit commands.

## Run the API

ClaimLens uses PostgreSQL; SQLite is not supported.

```bash
createdb claimlens
set -a; . ./.env; set +a
make run
```

The service listens on `http://127.0.0.1:8000` and exposes:

- `POST /classify`
- `POST /evaluation-runs` with `agent_version` (`v1` or `v2`) and `split`
  (`development` or `check`)
- `GET /evaluation-runs/{id}`

The API/TestClient and PostgreSQL integration suites passed, but a persistent
live `make run` process smoke was not retained. Therefore that smoke is
**INCOMPLETE**, not **VERIFIED**; TestClient integration is not being called a
deployed-process test.

## Docker

The Dockerfile uses Python `3.12.7` and the locked dependencies:

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

The recorded corrected audit reports a successful image build, 97 container
tests with 5 PostgreSQL-dependent tests skipped without a DB URL, and 10
container DB/API integration tests with 1 warning. No hosted deployment is
required by the assessment; hosted deployment remains **UNVERIFIED**.

## Tests and offline verification

The following commands have retained receipts or are the exact offline checks
used for the current evidence:

```bash
make test
make verify
make hash
PYTHONPATH=. .venv/bin/python evidence/audit/repository_audit.py
PYTHONPATH=. .venv/bin/python evidence/langfuse-investigation/audit.py \
  --offline --output evidence/langfuse-investigation/audit-report.json
```

Current receipts:

| Check | Result | Evidence |
|---|---|---|
| `make test` | **VERIFIED — 102 passed, 1 warning** | `evidence/pytest-output.txt` |
| `make verify` | **VERIFIED — exit 0; both corrected development metric objects matched** | `evidence/verify-output.txt` |
| PostgreSQL/API integration | **VERIFIED — 24 passed, 1 warning** | `tests/integration/`, `tests/api/` and handoff receipt |
| Docker build | **VERIFIED — passed** | `Dockerfile` and recorded build receipt |
| Docker container tests | **VERIFIED — 97 passed, 5 skipped** without DB env | recorded container receipt |
| Focused offline gate | **VERIFIED — 100 passed, 1 warning** | recorded focused-gate receipt |
| No-credentials FastAPI process smoke | **VERIFIED as a configuration/validation smoke** | `evidence/run-smoke.txt` |
| Live `make run` process smoke | **INCOMPLETE — no `make run` receipt retained** | Direct Uvicorn smoke did not send a valid provider request or reach a configured DB |

`make verify` performs no provider calls. It rescored the two corrected
development artifacts and matched their stored metrics exactly. The selected
manifest is not a full repository-tree hash; its scope is documented in
`evidence/MANIFEST.json`.

## Corrected final live runs

The historical frozen-tag runs remain preserved. The four corrected final
artifacts below are new directories and new run IDs; no historical artifact was
overwritten. All four use post-fix source revision
`a0020d06e0d71fbe83fd43236cdec3b33983d8b6` and the same provider, model,
dataset, evaluator, timeout, retry, turn, and concurrency settings. The v2
prompt is the intended model-facing behavior change from v1.

| Run | Run ID | Artifact | Answers | Errors | Predictions | Joint F1 |
|---|---|---|---:|---:|---:|---:|
| v1 development | `1d30770a-933b-4982-83be-875d24c02682` | `artifacts/v1-development-corrected-final/` | 22/40 ok | 18 | 201 | 0.665511 |
| v2 development | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `artifacts/v2-development-corrected-final/` | 26/40 ok | 14 | 231 | 0.714992 |
| v1 check | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `artifacts/v1-check-corrected-final/` | 5/10 ok | 5 | 44 | `null` |
| v2 check | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `artifacts/v2-check-corrected-final/` | 9/10 ok | 1 | 83 | `null` |

### Corrected development headline metrics

The development split has 376 gold claims. `P` is accepted predictions, `M`
is exact span matches, and `C` is exact span matches with the correct label.

| Metric | v1 | v2 |
|---|---:|---:|
| Predictions (`P`) | 201 | 231 |
| Matched spans (`M`) | 201 | 231 |
| Correct labels (`C`) | 192 | 217 |
| Extraction precision | 1.000000 | 1.000000 |
| Extraction recall | 0.534574 | 0.614362 |
| Extraction F1 | 0.696707 | 0.761120 |
| Joint precision | 0.955224 | 0.939394 |
| Joint recall / coverage (`C/G`) | 0.510638 | 0.577128 |
| Joint F1 | 0.665511 | 0.714992 |
| Macro label F1 | 0.665583 | 0.714444 |

Observed joint-F1 delta is `+0.049480`. The paired bootstrap estimate is
`+0.068996` with a 95% CI of `[-0.086781, +0.220426]` from 2,000 paired
resamples over 40 answers with seed `0`; the interval includes zero. The
comparison is therefore an observed result, not statistically conclusive
generalization. Claim transitions are in
`evidence/v1-v2-final-comparison.json`; no final-check gold labels were used
or inspected.

### Corrected predictions and errors

- v1 development: 18 `provider_timeout` outcomes on A01, A02, A04, A06,
  A07, A09, A11, A12, A17, A22, A28, A30, A32, A33, A34, A37, A39, and
  A40.
- v2 development: 13 `provider_timeout` outcomes on A03, A04, A06, A08,
  A11, A17, A29, A31, A33, A34, A37, A39, and A40; A09 was an
  `invalid_output` (`claim fields are incomplete or unknown`).
- v1 check: five `provider_timeout` outcomes on A43, A44, A46, A48, and A50.
- v2 check: one `provider_timeout` outcome on A46.

These are answer-level errors. A timeout creates no accepted predictions and
counts every gold claim for that development answer as a missed `G` claim; it
is not converted into an `unknown` prediction. The implementation retains the
specification's 15-second limit for each provider request, with at most two
attempts per turn and at most three model turns. This documentation pass did
not change timeouts, retries, artifacts, or code.

Provider timeouts are not labelled as rate limits or quota exhaustion. They are
distinct from the separate HTTP 429 responses seen by the post-run Langfuse
audit. The live provider runs also used a non-premium account; that account
tier is a documented limitation of this evidence, not a proven explanation for
the answer timeouts. The cause of the run-to-run timeout variation is
**UNVERIFIED**.

### Check-score isolation

The check reports retain predictions, errors, timings, and terminal answer
outcomes, but all gold-derived fields remain `null`: `gold_claims`, matched
spans, correct labels, extraction and joint precision/recall/F1, coverage,
per-label metrics, macro F1, confusion matrices, and missed/unmatched/wrong
label ID lists. Check quality is intentionally `null`, not zero. The hiring
team holds the check labels.

## Advice, instruction, and injection evidence

The historical frozen v1 analysis
(`artifacts/v1-development-final-turn-gated/`, run
`8cb56eec-6c54-4eab-aba2-317bee75d451`) found 605 predictions and 261
unmatched predictions: 254 advice/instruction-only spans, 3 explicit
meta-instruction markers, 2 factual-looking supported sentences, and 2
unsupported/unresolved sentences. The saved historical v1 and v2 injection
canaries both failed for A07, A18, and A32 because the marker sentence was
extracted; `override_suspected` was false and not all labels were `supported`.
See `evidence/analysis/v1-development-final-turn-gated-analysis.json`,
`evidence/v1-final-injection-canary.json`, and
`evidence/v2-final-injection-canary.json`.

The corrected development reports have zero unmatched accepted prediction IDs
in both runs, and the saved corrected diagnostic advice-only count is zero.
Offline tests cover pure advice, opinions, instructions, prompt injection,
imperative-plus-fact sentences, Unicode spans, and exact output validation.
Those current saved-run and offline results do not turn the historical failed
canaries into passes; the historical canary files remain preserved.

The evidence supports the narrower claim that the intended v2 prompt policy
was based on the saved v1 development analysis, that no private check labels or
check-quality metrics were loaded or inspected, and that no direct evidence was
found that an individual filter token was added because of a check-split output.
Because the answer inputs were visible and no external decision log exists, the
repository cannot independently confirm that no undocumented human decision
considered visible check text. It therefore does not claim a blind benchmark or
claim that no check-informed tuning of any kind occurred.

## Langfuse evidence and unfinished work

Each corrected final run has one local trace receipt per scheduled answer,
generation/tool events, answer diagnostics, and development score events where
applicable. The corrected verifier uses each per-answer trace ID and an
explicit UTC run window; the historical `timedelta` verifier failure is
preserved in `artifacts/v2-development-final/trace_verification_error.json`.
The four corrected final runs have no `trace_verification_error.json`, and
their local receipt rows are non-empty.

The later retained sanitized audit
(`evidence/langfuse-investigation/audit-report.json`, generated at
2026-10-19T22:46:50Z) initially queried all 100 corrected trace IDs and
recorded 86 non-empty observation responses plus 14 HTTP 429 responses. The
subsequent serial re-query retained in `evidence/langfuse/langfuse-evidence.md`
and `evidence/langfuse/langfuse-summary.json` recovered all 14: final remote
confirmation is v1 development 40/40, v2 development 40/40, v1 check 10/10,
and v2 check 10/10, with no remaining inaccessible traces. The 429s were
Langfuse audit responses, not provider answer timeouts or quota evidence.

The current reviewer export is sanitized observation metadata, not a full raw
payload export. It intentionally omits full prompts/answers, model outputs,
tool payloads, score payloads, credentials, and environment values.

The exact unfinished work is:

- **INCOMPLETE:** retain full reviewer-facing remote Langfuse payload exports
  for all 100 corrected traces. The repository retains only local receipts and
  sanitized counts, observation names, status codes, and body digests; it does
  not retain prompts, model/tool payloads, scores, or credentials.
- **INCOMPLETE:** retain a persistent live `make run` process smoke. A direct
  no-credentials Uvicorn process smoke is retained in `evidence/run-smoke.txt`,
  but it stopped at request-validation/configuration blockers and was not a
  `make run` smoke against a configured provider/database.
- **UNVERIFIED:** hosted deployment, which the assessment does not require.
- **UNVERIFIED:** blind check quality and the cause of the differing timeout
  rates.

## Sequence and deviations

The list below is chronological by saved `started_at` (UTC); all entries are
from 2026-10-19. It includes the
diagnostic runs that drove bounded-runtime changes, the early frozen runs, the
first corrected attempt, and the four current final runs. “Superseded” means
the artifact is intentionally retained but is not used by the current final
comparison.

In this table, **VERIFIED** describes the saved artifact and its local
accounting; it does not make private check quality available.

The early tags `v1-frozen` and `v2-frozen` both point to `a9ce77e`; their first
full/check artifacts were error-heavy and were superseded. The later final
freeze tags point to `v1-final-frozen` = `9de1a5ec...` and
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
| 21:16 | `ec01ca09-4d16-4261-916f-0902d16b2e89` | `artifacts/v1-development-corrected/` | First corrected development attempt after shared validation/adversarial extraction fixes. | **INCOMPLETE/SUPERSEDED** — 18/40 ok; 162 predictions; `AttributeError: 'str' object has no attribute 'isoformat'` in trace-window verification. |
| 21:34 | `1d30770a-933b-4982-83be-875d24c02682` | `artifacts/v1-development-corrected-final/` | Rerun after the trace-window string fix; prior corrected provider outputs preserved. | **CURRENT FINAL/VERIFIED** — 22/40 ok; 201 predictions; local receipts complete. |
| 21:52 | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `artifacts/v2-development-corrected-final/` | Corrected v2 rerun after shared validation/adversarial extraction fixes. | **CURRENT FINAL/VERIFIED** — 26/40 ok; 231 predictions; local receipts complete. |
| 22:09 | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `artifacts/v1-check-corrected-final/` | Corrected v1 check; historical check preserved. | **CURRENT FINAL/VERIFIED** — 5/10 ok; 44 predictions; quality `null`. |
| 22:14 | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `artifacts/v2-check-corrected-final/` | Corrected v2 check; historical check preserved. | **CURRENT FINAL/VERIFIED** — 9/10 ok; 83 predictions; quality `null`. |

### Why the sequence deviated

The assessment's intended order was v1 development, one targeted v2 change,
then frozen v1/v2 checks. The first full runs and early checks were preserved
because provider errors and bounded-execution behavior required diagnosis
before a meaningful comparison. The first check outputs were error-heavy: v1
was 6/10 with no predictions and v2 was 2/10 with no predictions. After those
outputs were seen, the saved v1 development analysis and failed injection
canaries also made it unsafe to present the earlier artifacts as the corrected
submission. The pipeline therefore changed shared validation,
advice/instruction filtering, gold isolation, and trace verification, then
reran both development versions and both checks from one post-fix source
revision. The checks have no quality labels, so this was not a check-score
optimization.

The success rates are not stable across the retained live runs: both earlier
frozen development runs were 37/40 ok, while the corrected v1 and v2
development runs were 22/40 and 26/40 ok. The cause of that variation is
unproven. The 15-second per-request limit and retry policy were not changed to
explain it, and the account-tier limitation is disclosed above. No historical
artifact was overwritten.
