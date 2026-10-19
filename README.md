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
- `evidence/`: selected manifest, audit, Langfuse and submission evidence.

## Setup

Python `3.12.7` is pinned in `.python-version`; dependencies are pinned in
`requirements.lock`.

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

No model/API call is needed for the tests, evaluator, verification, or offline
audit commands.

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

Example:

```bash
curl -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"answer_id":"A01","brand_id":"cleartrail","question":"Which facts?","ai_answer":"ClearTrail Mini costs INR 1,299 in India.","agent_version":"v1"}'
```

## Docker

The Dockerfile uses Python `3.12.7` and the locked dependencies:

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

Docker Desktop was available for this corrected audit. The image build passed;
the container test suite passed with PostgreSQL-only tests skipped unless a DB
URL is supplied. No hosted deployment is required by the assessment.

## Tests and offline verification

```bash
make test
make verify
make hash
PYTHONPATH=. .venv/bin/python evidence/audit/regenerate_evidence.py
PYTHONPATH=. .venv/bin/python evidence/audit/repository_audit.py
```

Current receipts:

| Check | Result | Evidence |
|---|---|---|
| `make test` | **102 passed, 1 warning** | `evidence/pytest-output.txt` |
| `make verify` | **exit 0; both corrected development reports matched** | `evidence/verify-output.txt` |
| PostgreSQL/API integration with `DATABASE_URL` | **24 passed, 1 warning** | `tests/integration/`, `tests/api/` |
| Docker build | **passed** | `Dockerfile`, Docker build receipt |
| Docker test suite | **97 passed, 5 skipped** without DB env | Docker run receipt |
| Focused offline gate | **100 passed, 1 warning** | agent/reliability/Langfuse/API/integration tests |

`make verify` performs no provider calls. It rescored the two corrected
development artifacts below and matched their stored metrics exactly.

## Corrected final live runs

The historical frozen-tag runs remain preserved. Because the shared agent and
validation behavior were corrected, the four current final artifacts are new
directories and new run IDs; no historical artifact was overwritten.

| Run | Run ID | Source revision | Answers | Errors | Predictions | Joint F1 |
|---|---|---|---:|---:|---:|---:|
| v1 development | `1d30770a-933b-4982-83be-875d24c02682` | `a0020d0` | 22/40 ok | 18 | 201 | 0.665511 |
| v2 development | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `a0020d0` | 26/40 ok | 14 | 231 | 0.714992 |
| v1 check | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `a0020d0` | 5/10 ok | 5 | 44 | null |
| v2 check | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `a0020d0` | 9/10 ok | 1 | 83 | null |

Artifacts:

```text
artifacts/v1-development-corrected-final/
artifacts/v2-development-corrected-final/
artifacts/v1-check-corrected-final/
artifacts/v2-check-corrected-final/
```

Development headline metrics:

| Metric | v1 | v2 |
|---|---:|---:|
| Extraction precision | 1.000000 | 1.000000 |
| Extraction recall | 0.534574 | 0.614362 |
| Extraction F1 | 0.696707 | 0.761120 |
| Joint precision | 0.955224 | 0.939394 |
| Joint recall / coverage | 0.510638 | 0.577128 |
| Joint F1 | 0.665511 | 0.714992 |
| Macro label F1 | 0.665583 | 0.714444 |

Observed joint-F1 delta: `+0.049480`. The paired bootstrap estimate is
`+0.068996` with 95% CI `[-0.086781, +0.220426]`; the interval includes zero.
All claim transitions are in `evidence/v1-v2-final-comparison.json`; do not
treat the observed gain as statistically conclusive. No final-check gold labels
were used or inspected.

The corrected v1/v2 development runs share the same post-fix source revision,
dataset, evaluator, provider, model, settings, and execution rules. The only
model-facing behavior change is the v2 prompt's advice/instruction policy.
This is a clean corrected rerun, not a claim that the historical frozen-tag
runs were rewritten.

## Advice/instruction behavior

The old historical v1/v2 runs extracted hundreds of advice-only spans and both
historical injection canary files failed. The corrected agent now filters pure
advice, opinions, imperative instructions, and meta-instructions while retaining
sentences with an independent factual clause. Representative adversarial tests
cover pure advice, opinions, prompt injection, imperative-plus-fact sentences,
Unicode spans, and exact output validation.

In the corrected development artifacts, every accepted prediction is an exact
gold span (`unmatched_prediction_ids` is empty in both reports). The diagnostic
advice-only count is therefore zero for both corrected runs. The older counts
and failed canaries remain in the historical evidence and are not overwritten.

## Final-check errors and score isolation

Errors are recorded as they occurred; a timeout is not labelled as a rate-limit
or quota failure:

- corrected v1 development: 18 `provider_timeout` outcomes;
- corrected v2 development: 13 `provider_timeout` outcomes and one
  `invalid_output` (`claim fields are incomplete or unknown`);
- corrected v1 check: five `provider_timeout` outcomes (A43, A44, A46, A48,
  A50);
- corrected v2 check: one `provider_timeout` outcome (A46).

Both check reports retain accepted predictions and errors while keeping gold
claims, matches, correctness, extraction/joint metrics, coverage, per-label
metrics, macro F1, and confusion matrices null.

## Langfuse evidence

Each corrected final run has one local trace receipt per scheduled answer,
generation/tool events, answer diagnostics, and development score events where
applicable. The verifier now uses each per-answer trace ID and an explicit UTC
run window; the historical `timedelta` `NameError` is fixed. The corrected four
runs have no `trace_verification_error.json` and all local receipt rows are
non-empty.

The read-only audit tool is:

```bash
PYTHONPATH=. .venv/bin/python evidence/langfuse-investigation/audit.py \
  --offline --output evidence/langfuse-investigation/audit-report.json
```

The final read-only remote audit queried all 100 corrected trace IDs: 86
returned observations and 14 returned HTTP 429 responses. The 429s are recorded
as rate-limit blockers, not interpreted as missing traces. The audit preserves
only sanitized counts, names, status codes, and body digests; it never writes
credentials or raw model/tool payloads. Full reviewer-facing exports remain
**INCOMPLETE**.

## Reproducibility and status

- **VERIFIED:** pinned runtime and lockfile; evaluator; corrected agent tests;
  retry/429 behavior; genuine concurrency ceiling of three; gold-payload
  isolation; strict claim validation; four complete corrected artifact sets;
  check-score nulling; PostgreSQL/API integration; Docker build; offline
  verification; selected manifest hashes.
- **INCOMPLETE:** all-remote Langfuse trace retrieval/full sanitized exports;
  live `make run` process smoke; blind check quality because only the hiring
  team has check labels; statistical certainty of the observed improvement.
- **UNVERIFIED:** hosted deployment, which the assessment does not require.

Evidence entry points:

- `evidence/final-run-summary.json`
- `evidence/v1-v2-final-comparison.json`
- `evidence/submission/final-provenance.json`
- `evidence/audit/current-audit.json`
- `evidence/MANIFEST.json`
- `HANDOFF.md`

OmniRush.ai and delegated coding agents assisted with implementation and test
writing. Existing failed, superseded, and historical artifacts remain intact.
