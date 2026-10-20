# ClaimLens

ClaimLens extracts and classifies catalogue-grounded factual product claims from long AI answers.

## Summary

| Run | Answers OK | Errors | Predictions | Joint F1 |
|---|---:|---:|---:|---:|
| v1 development | 22/40 | 18 | 201 | 0.665511 |
| v2 development | 26/40 | 14 | 231 | 0.714992 |
| v1 check | 5/10 | 5 | 44 | `null` |
| v2 check | 9/10 | 1 | 83 | `null` |

The development headline is joint F1 `0.665511` for v1 and `0.714992` for
v2. The paired-bootstrap 95% confidence interval for the estimated gain is
`[-0.086781, +0.220426]`, so it includes zero.

### About the timeouts

The timeout errors are provider-request timeouts (the model did not return
within the specification's 15-second per-request limit); they are not
validation, scoring or logic failures, and the code handles them as designed:
as answer-level errors with at most one bounded retry per turn, counted as
missed gold claims, never relabelled as unknown. The likely contributor is
response latency on a non-premium API account; the exact cause is unproven
(the same code earlier completed 37/40 development answers, and bounded-call
fixes such as lower output caps and serialized post-tool turns did reduce
timeouts). The 15-second limit was kept per the specification.

v1 and v2 had different success counts (22/40 vs 26/40 development, 5/10 vs
9/10 check), so part of the observed gain may come from more answers
completing; the all-40 numbers are the official specification metrics.

Start here: [Quick start](#quick-start-no-api-key-needed) ·
[Targeted change](#the-targeted-change) · [Evidence map](#evidence-map)

## Quick start (no API key needed)

The saved-run verification and offline tests do not call a model or provider.
From a clean checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
make test
make verify
```

Expected results from the saved receipts:

- `make test`: **VERIFIED — 102 passed, 1 warning** (`evidence/pytest-output.txt`).
- `make verify`: **VERIFIED — exit 0; both corrected development metric objects matched** (`evidence/verify-output.txt`).

Python `3.12.7` is pinned in `.python-version`. The supplied local
`answers.csv`, `brand_facts.csv`, and `development_gold.csv` inputs are ignored
and are not committed; their hashes are recorded in `evidence/MANIFEST.json`.

## Settings used

The four corrected final artifacts use the following shared settings. The v1
and v2 prompt hashes differ; the source revision, provider settings, dataset,
fact catalogue, evaluator, execution limits, and concurrency settings are
shared.

| Setting | Value |
|---|---|
| Provider | `openai` |
| Model ID | `gpt-5-mini-2025-08-07` |
| Reasoning effort | `minimal` |
| Completion-token cap | `2000` |
| Per-request timeout | `15.0` seconds |
| Attempts per turn | `2` maximum; one retry |
| Maximum turns | `3` |
| Global provider concurrency | `3` |
| Post-tool turn serialization | Enabled for post-tool completion turns |
| Source revision | `a0020d06e0d71fbe83fd43236cdec3b33983d8b6` |
| Dataset version | `long-brand-answers-50-v4` |
| Fact version | `speaker-catalogue-v4` |
| Evaluator version | `sentence-spans-and-labels-v4` |

## The targeted change

### What the saved v1 analysis showed

The real v1 development analysis (`artifacts/v1-development-final-turn-gated/`,
run `8cb56eec-6c54-4eab-aba2-317bee75d451`) recorded 605 predictions and 261
unmatched predictions. The saved diagnosis classified those unmatched rows as
254 advice/instruction-only spans, 3 meta-instruction markers, 2
unsupported/unresolved spans, and 2 purchase-paperwork instructions. The source
analysis is `evidence/analysis/v1-development-final-turn-gated-analysis.json`.

### What v2 changes

- v1 is defined in `prompts/v1.txt`: extract complete verifiable product,
  specification, purchase-policy, historical, and comparison sentences while
  excluding pure advice, opinions, and instructions.
- v2 is defined in `prompts/v2.txt`: it makes the catalogue the sole authority,
  adds an explicit sentence-boundary pass, and says product or purchase wording
  alone is not a claim.
- v2 explicitly omits sentences that are only advice, instructions, or opinions,
  while preserving a sentence containing an independent factual assertion.
- Both prompts retain the exact JSON, Unicode-offset, non-overlap, label, and
  catalogue-fact rules; the corrected source revision also contains shared
  deterministic filtering and validation changes.

The v2 policy hypothesis and its offline basis are recorded in
`tests/v2_policy/report.md`. No provider experiment was launched for that
offline policy test.

### Measured effect

| Metric | v1 | v2 | Delta |
|---|---:|---:|---:|
| Extraction F1 | 0.696707 | 0.761120 | +0.064413 |
| Joint F1 | 0.665511 | 0.714992 | +0.049480 |
| Macro label F1 | 0.665583 | 0.714444 | +0.048861 |
| Correct-label coverage | 0.510638 | 0.577128 | +0.066489 |

Both final development reports have extraction precision `1.000000`; there is
therefore no extraction-precision change to attribute. The complete comparison
is `evidence/v1-v2-final-comparison.json`.

### Per-label results and regressions

| Label | v1 precision | v1 recall | v1 F1 | v2 precision | v2 recall | v2 F1 |
|---|---:|---:|---:|---:|---:|---:|
| `contradicted` | 0.924242 | 0.508333 | 0.655914 | 0.886076 | 0.583333 | 0.703518 |
| `supported` | 0.954545 | 0.525000 | 0.677419 | 0.944444 | 0.566667 | 0.708333 |
| `unknown` | 0.985507 | 0.500000 | 0.663415 | 0.987500 | 0.580882 | 0.731481 |

No per-label F1 regression is marked in the saved comparison. Per-label
precision decreased for `contradicted` (`-0.038166`) and `supported`
(`-0.010101`), while their recall and F1 increased; `unknown` precision
increased by `+0.001993`.

At claim level, the comparison contains 65 fixed claim IDs and 40 regressed
claim IDs. The regressions occur across answers A03, A08, A16, A25, A29, and
A31; the comparison contains claim-state transitions rather than a separate
per-answer quality metric. The state-level details are in
`evidence/v1-v2-final-comparison.json` and the provenance summary is in
`evidence/submission/final-provenance.json`.

### Conclusion and attribution limits

The observed gain is small and descriptive: the bootstrap interval includes
zero, and the development runs had different numbers of completed answers.
The evidence supports that the saved v1 analysis motivated the v2
advice/instruction policy, and that corrected runs contain no unmatched
accepted prediction IDs. It does not isolate the causal contribution of the
v2 prompt from the shared deterministic advice/instruction filter and
validation changes. Because extraction precision was `1.000000` in both final
runs, the evidence does not show that either change explains an extraction-
precision improvement.

## Run and API use

The current corrected final artifacts are:

```text
artifacts/v1-development-corrected-final/
artifacts/v2-development-corrected-final/
artifacts/v1-check-corrected-final/
artifacts/v2-check-corrected-final/
```

For live operation, PostgreSQL and provider credentials are required. Copy
`.env.example` to `.env`, set the live values, create the database, and run:

```bash
createdb claimlens
set -a; . ./.env; set +a
make run
```

The service exposes `POST /classify`, `POST /evaluation-runs`, and
`GET /evaluation-runs/{id}`. The no-credentials process smoke in
`evidence/run-smoke.txt` is **VERIFIED as a configuration/validation smoke**:
it exercised validation/configuration blockers only and did not send a valid
provider request.

## Docker

The project pins Python `3.12.7` and dependencies in `requirements.lock`.

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

The saved **VERIFIED** receipt records a successful image build, 97 container
tests with 5 PostgreSQL-dependent tests skipped without a database URL, and 10
container database/API integration tests with 1 warning.

## Evidence map

| Rubric area | Implementation, tests, and evidence |
|---|---|
| Agent | `agent/core.py`; `tests/agent/test_core.py`; `tests/agent/test_import_isolation.py`; `artifacts/v1-development-corrected-final/outcomes.json`; `artifacts/v2-development-corrected-final/outcomes.json`; `artifacts/v1-development-corrected-final/predictions.json`; `artifacts/v2-development-corrected-final/predictions.json` |
| Evaluation harness | `harness/evaluator.py`; `harness/reports.py`; `harness/comparison.py`; `tests/harness/test_evaluator.py`; `tests/harness/test_reports_comparison.py`; corrected `report.json` files |
| Improvement/regression analysis | `prompts/v1.txt`; `prompts/v2.txt`; `tests/v2_policy/report.md`; `evidence/analysis/v1-development-final-turn-gated-analysis.json`; `evidence/v1-v2-final-comparison.json`; `evidence/submission/final-provenance.json` |
| FastAPI/PostgreSQL | `claimlens/api/app.py`; `claimlens/api/deps.py`; `claimlens/db/schema.py`; `claimlens/db/repository.py`; `tests/api/`; `tests/integration/test_api_postgres.py`; `evidence/run-smoke.txt` |
| Langfuse | `claimlens/tracing.py`; `runner/experiment.py`; `tests/langfuse/test_verifier.py`; `tests/reliability/test_tracing.py`; `artifacts/v1-development-corrected-final/trace_verification.json`; `artifacts/v2-development-corrected-final/trace_verification.json`; `artifacts/v1-check-corrected-final/trace_verification.json`; `artifacts/v2-check-corrected-final/trace_verification.json`; `evidence/langfuse/langfuse-summary.json`; `evidence/langfuse/langfuse-evidence.md` |
| Reliability/tests | `runner/executor.py`; `providers/openai.py`; `tests/reliability/test_executor.py`; `tests/reliability/test_openai_provider.py`; `tests/agent/`; `evidence/pytest-output.txt` |
| Reproducibility | `.python-version`; `requirements.lock`; `Dockerfile`; `Makefile`; `evidence/MANIFEST.json`; `evidence/audit/current-audit.json`; `evidence/audit/REPORT.md` |

## Disclosure

AI assistance used: OmniRush with subagents. The live provider was OpenAI
`gpt-5-mini` (`gpt-5-mini-2025-08-07`).

Checks with retained receipts include:

- `make test`: `evidence/pytest-output.txt` — **VERIFIED**, 102 passed and 1 warning.
- `make verify`: `evidence/verify-output.txt` — **VERIFIED**, exit 0.
- Docker build and container tests: the saved Docker receipts summarized in `HANDOFF.md`.
- PostgreSQL/API integration: `tests/integration/test_api_postgres.py`, `tests/api/`, and the saved receipt summarized in `HANDOFF.md`.
- Langfuse audit: `evidence/langfuse-investigation/audit-report.json`, `evidence/langfuse/langfuse-summary.json`, and `evidence/langfuse/langfuse-evidence.md`.

Elapsed time: **9:54:07.501173 wall-clock**, from the first saved run artifact
(`artifacts/smoke-v1-A01/run.json`, `2026-10-19T12:23:01.785086Z`) to the last
saved run artifact (`artifacts/v2-check-corrected-final/run.json`,
`2026-10-19T22:17:09.286259Z`), including waiting.

## Known limitations and unfinished work

- **INCOMPLETE:** Full reviewer-facing Langfuse payload exports for all 100
  corrected traces were not retained. The repository retains sanitized
  observation metadata, local receipts, counts, names, status codes, and body
  digests, not prompts, model/tool payloads, score payloads, credentials, or raw
  response bodies.
- **INCOMPLETE:** A persistent configured `make run` process smoke was not
  retained. `evidence/run-smoke.txt` is a no-credentials validation/
  configuration smoke and did not reach a valid provider/database path.
- **UNVERIFIED:** Blind check quality. Check reports intentionally keep
  gold-derived quality fields `null`; the check labels are held by the hiring
  team.
- **UNVERIFIED:** The exact cause of the timeout and success-rate variation.
  The non-premium account is a documented limitation, not a proven cause.
- **UNVERIFIED:** Hosted deployment, which is not required by the assessment.

## Appendix: sequence and deviations

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
