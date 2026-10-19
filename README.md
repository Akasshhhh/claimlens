# ClaimLens

ClaimLens is a Python agent and evaluation harness for extracting factual
sentences from long product answers, grounding them against a supplied product
catalogue, and classifying each sentence as `supported`, `contradicted`, or
`unknown`.

The implementation uses real OpenAI tool calling, strict Python-side Unicode
span validation, bounded asynchronous execution, deterministic scoring, a thin
FastAPI service, PostgreSQL persistence, and Langfuse observations.

## Architecture

```text
answers.csv ───────┐
brand_facts.csv ───┼─> CsvFactStore ─> ClaimAgent
                   │                     │
question/answer ──┘                     ├─ get_brand_facts tool call
                                         ├─ validated claims/spans
                                         └─ Langfuse trace

OpenAIProvider -> BoundedProvider/AsyncExecutor -> AnswerOutcome
                                           │
                                           ├─ harness/evaluator.py -> reports
                                           ├─ PostgreSQL runs/answers/claims
                                           └─ immutable artifacts/<run>/
```

Key directories:

- `agent/`: tool loop, answer-level errors, exact span/quote/fact validation.
- `prompts/`: frozen v1 and candidate v2 prompts.
- `providers/`: official OpenAI SDK adapter.
- `runner/`: global three-attempt concurrency ceiling and live run artifacts.
- `harness/`: exact-span matching, extraction/joint metrics, comparisons.
- `claimlens/api/`: FastAPI routes.
- `claimlens/db/`: PostgreSQL schema, repository, and artifact importer.
- `evidence/`: analysis, audit output, hashes, comparisons, and run summaries.

## Requirements and setup

Python 3.12.7 is pinned in `.python-version`; exact dependencies are in
`requirements.lock`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
```

Provide values locally for:

- `OPENAI_API_KEY`
- `OPENAI_MODEL` (the recorded assessment runs used `gpt-5-mini-2025-08-07`)
- `DATABASE_URL`
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL`

The supplied `answers.csv`, `brand_facts.csv`, and `development_gold.csv` are
kept as local assessment inputs and are not staged by default. Put them in the
repository root or set `CLAIMLENS_DATA_DIR`. Their exact SHA-256 values are
recorded in `evidence/MANIFEST.json`.

## Running the service

Create/use a PostgreSQL database first, then start the API:

```bash
createdb claimlens
export DATABASE_URL=postgresql://claimlens:claimlens@localhost:5432/claimlens
make run
```

Classify one answer:

```bash
curl -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"answer_id":"A01","brand_id":"cleartrail","question":"...","ai_answer":"...","agent_version":"v1"}'
```

The service also exposes:

- `POST /evaluation-runs` with `agent_version` and `split` (`development` or
  `check`).
- `GET /evaluation-runs/{run_id}` for stable answer/claim retrieval.

SQLite is not supported; persistence uses PostgreSQL and parameterized SQL.

## Tests and offline verification

```bash
make test
make verify
make hash
```

Verified in this workspace:

```text
make test    -> 66 passed, 1 warning
make verify  -> exit 0; saved development metrics matched exactly
tests/api    -> 20 passed with DATABASE_URL loaded
```

`make verify` does not call a model. It re-scores the committed corrected v1
and v2 development artifacts using the deterministic evaluator.

## Actual assessment results

The original failed attempts are preserved. The final controlled sequence used
the frozen revisions `v1-final-frozen` and `v2-final-frozen`.

| Run | Run ID | Answers | Errors | Predictions | Joint F1 |
|---|---|---:|---:|---:|---:|
| v1 development | `8cb56eec-6c54-4eab-aba2-317bee75d451` | 37/40 ok | 3 | 605 | 0.660550 |
| v2 development | `5a64e5ff-d1e4-4e49-8175-e2c3617c26cb` | 37/40 ok | 3 | 610 | 0.679513 |
| v1 check | `93af5381-5900-43f5-8543-29e32e1ee64f` | 9/10 ok | 1 | 143 | null |
| v2 check | `a58a7b95-2784-4184-abd5-2161a9386028` | 7/10 ok | 3 | 117 | null |

Development headline metrics:

| Metric | v1 | v2 |
|---|---:|---:|
| Extraction F1 | 0.701325 | 0.705882 |
| Joint F1 | 0.660550 | 0.679513 |
| Macro label F1 | 0.743466 | 0.769791 |
| Gold coverage | 0.861702 | 0.890957 |

The measured joint-F1 delta was `+0.018963`. The paired bootstrap estimate was
`+0.015324` with 95% CI `[-0.046936, +0.083638]`; the interval includes zero.
The v2 change targeted the v1 finding that 254 of 261 unmatched predictions
were advice/instruction-only spans.

## Evidence

Required final artifacts:

```text
artifacts/v1-development-final-turn-gated/
artifacts/v2-development-final/
artifacts/v1-check-final/
artifacts/v2-check-final/
```

Supporting evidence:

- `HANDOFF.md`: detailed provenance, limitations, disclosure, and rubric check.
- `evidence/final-run-summary.json`: run IDs, counts, metrics, trace counts.
- `evidence/v1-v2-final-comparison.json`: per-answer/tag/label comparison,
  claim flips, and bootstrap interval.
- `evidence/analysis/`: offline v1 baseline/error analysis.
- `evidence/langfuse-investigation/`: host/query/trace investigation.
- `evidence/MANIFEST.json`: CSV, prompt, artifact, and evidence hashes.

Every final run has one terminal answer outcome per scheduled answer and fetched
Langfuse observation receipts. Check reports intentionally keep all gold-based
metrics null.

## Reproducibility and limitations

- Model settings were fixed across the corrected v1/v2 runs: OpenAI,
  `gpt-5-mini-2025-08-07`, `reasoning_effort=minimal`,
  `max_completion_tokens=2000`, no temperature, 15-second per-attempt
  timeout, maximum two attempts per turn, maximum three turns per answer, and
  maximum three concurrent provider attempts.
- The final-check inputs are visible to the local operator; this is disclosed
  and is not claimed to be a blind benchmark.
- Docker build was attempted but blocked because the local Docker daemon was
  not running. `make run` was not executed in this session.
- No hosted deployment was required or performed.
- Superseded exploratory artifacts remain locally for auditability; they are
  not part of the four required final run set.

## Security

Credentials belong only in the ignored local `.env`. Do not commit `.env`, API
keys, database passwords, Langfuse secrets, virtual environments, caches, or
temporary experiment output.
