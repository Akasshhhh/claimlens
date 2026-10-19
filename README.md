# ClaimLens

ClaimLens extracts complete factual sentences from long product answers, looks up only the requested catalogue brand through a real OpenAI tool call, validates exact Unicode spans in Python, and scores development predictions deterministically.

## Setup

Python 3.12.7 is pinned in `.python-version`. The supplied `answers.csv`, `brand_facts.csv`, and `development_gold.csv` are local assessment inputs and are intentionally not committed. Copy them into the repository root, or set `CLAIMLENS_DATA_DIR` to their directory.

```bash
cp .env.example .env
make install
```

Set `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-5-mini`), and `DATABASE_URL` in the environment. Four live experiments additionally require all three Langfuse variables. No credentials belong in this repository.

## Run and test

```bash
make test
make run
# in another shell
curl -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"answer_id":"A01","brand_id":"cleartrail","question":"...","ai_answer":"...","agent_version":"v1"}'
```

`make verify` re-scores saved development artifacts without calling a model. `make hash` prints the input SHA-256 values.

## PostgreSQL

Use a PostgreSQL database available at `DATABASE_URL`, for example:

```bash
createdb claimlens
export DATABASE_URL=postgresql://claimlens:claimlens@localhost:5432/claimlens
```

The API creates the required tables on startup when configured. SQLite is not supported by the persistence tests or application.

## Evaluation sequence

The runner writes immutable JSONL artifacts under `artifacts/`: v1 development, v2 development, v1 check, and v2 check. Development artifacts are scored only after predictions are saved; check artifacts never join gold or report quality metrics. The v2 prompt changes one documented behavior from v1, and both versions use the same provider, model, settings and fact catalogue.

## Reproducibility

Exact dependencies are pinned in `requirements.lock`; the Dockerfile provides a clean Linux build. `HANDOFF.md` records actual commands, evidence paths, gates, limitations, and any missing live-run evidence. Langfuse ingestion is best effort inside a run but live runs are refused unless credentials are configured and a test trace can be fetched back.
