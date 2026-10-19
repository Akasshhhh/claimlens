# ClaimLens handoff

## Result

ClaimLens is implemented on branch `wip/cli-handoff`. The repository contains
the shared contracts/loaders, live OpenAI tool-calling agent, bounded async
executor, deterministic evaluator, FastAPI/PostgreSQL layer, Langfuse tracer,
runner, immutable JSON artifacts, and offline verification command.

The four required live runs were executed in order after the v1/v2 freeze:

| run | run ID | evidence | result |
|---|---|---|---|
| v1 development A01–A40 | `30beb6e6-a7e3-4a2b-8a15-9f682b3cd076` | `artifacts/v1-development-rerun/` | 40 terminal provider-timeout errors, 0 predictions, joint F1 0.0 |
| v2 development A01–A40 | `916ec3da-3b4a-49e2-9fd7-1efa4386ca0e` | `artifacts/v2-development/` | 40 terminal provider-timeout errors, 0 predictions, joint F1 0.0 |
| v1 check A41–A50 | `200802cf-9bc8-4186-ae9a-9a95533fbf8d` | `artifacts/v1-check/` | 10 answers, 6 ok, 4 errors, 0 predictions; quality metrics null |
| v2 check A41–A50 | `ea08c511-28fb-4f69-a8ac-bc963417673b` | `artifacts/v2-check/` | 10 answers, 2 ok, 8 errors, 0 predictions; quality metrics null |

The required escalation was also run before the full experiments:
`artifacts/v1-escalation-3/` contains A01–A03 with 2 successful empty outputs
and one invalid-output error. Earlier failed infrastructure attempts remain
as evidence and are not presented as successful runs.

## Targeted change

The v1 trace diagnosis showed a real `get_brand_facts` call followed by valid
JSON with an empty claims list. The largest observed error class was extraction
omission, not scorer matching or label validation. v2 therefore differs from
v1 only by a prompt instruction requiring a complete sentence-boundary pass,
requiring every qualifying sentence to be extracted, and forbidding an empty
list when qualifying factual sentences exist. The measured effect on the
development run was no gain: v1 joint F1 `0.0`, v2 joint F1 `0.0`; paired
bootstrap delta estimate and 95% interval were all `0.0`.

The v1/v2 comparison, per-answer deltas, per-label regressions, difficulty-tag
recall, claim flip table, and bootstrap result are in
`evidence/v1-v2-comparison.json`. The development-only canaries for A07, A18,
and A32 are in `evidence/v1-injection-canary.json` and
`evidence/v2-injection-canary.json`.

## Exact commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
# Put the supplied CSVs in the repository root or set CLAIMLENS_DATA_DIR.
# Set OPENAI_API_KEY, OPENAI_MODEL, DATABASE_URL and Langfuse variables in .env.
make test
make verify
make hash
make run
```

`make test` was run and passed: **57 passed, 1 warning**. `make verify` was
run after the live development artifacts and re-scored both saved development
runs without API calls; both stored metric objects matched exactly. `make hash`
was run and produced the CSV hashes recorded in `evidence/MANIFEST.json`.

The API uses PostgreSQL only. With the configured `DATABASE_URL`,
`set -a; . ./.env; set +a; .venv/bin/pytest -q tests/api` passed **20 tests**.
No SQLite substitute was used. `make run` and a Docker build were not run in
this session; the Dockerfile and API target are present for the next operator.

## Langfuse verification

The Langfuse health check returned HTTP 200 with version `4.63.0`.
Trace verification uses the supported
`GET /api/public/v2/observations` endpoint with a trace ID, a small limit, and
minimal fields. It polls every 5 seconds for up to 120 seconds with a 45-second
request timeout; empty 200 responses, timeouts, and 5xx responses remain
pending, while other 4xx responses are reported as permanent failures. Ingestion
is asynchronous; run teardown waits for queued ingestion before polling.

All four final run artifact directories contain `trace_verification.json` with
successful observation counts. A sanitized trace diagnosis is in
`evidence/trace-diagnosis.json`.

## Reproducibility and limitations

- Runtime is pinned by `.python-version`; dependencies are pinned in
  `requirements.lock`; system build instructions are in `Dockerfile`.
- The supplied CSVs, `.env`, and credentials remain uncommitted. Only their
  SHA-256 hashes are recorded.
- The exact selected provider/model settings in the runs are OpenAI,
  `gpt-5-mini-2025-08-07`, `reasoning_effort=low`, no temperature, and a
  15-second per-attempt timeout.
- Development and check inputs are visible to this local operator. No claim is
  made that the final-check benchmark is blind.
- The live model produced no accepted claims in these runs and the measured
  quality is therefore zero. This is reported as evidence, not corrected or
  hidden.
- Run-to-run noise was not measured beyond the required runs. Native Langfuse
  experiments and screenshots were not added; API-fetched observation evidence
  is retained instead.
- Time spent was not tracked by the inherited session, so no fabricated total
  is reported. Per-answer and split timings are in each artifact's
  `timings.json` and `run.json`.

## Disclosure

OmniRush.ai and delegated coding agents assisted with implementation and test
writing. I verified the shared contracts, CSV parsing and hashes, OpenAI live
tool call, PostgreSQL connectivity and persistence tests, Langfuse health and
trace fetch-back, the full pytest run, the four live-run artifact directories,
and the offline metric re-score myself in this session.

## Evidence checklist

- Evaluator (20): **verified** — `harness/`, `tests/harness/`,
  `evidence/v1-v2-comparison.json`, and `make verify` output.
- Agent (15): **verified structurally** — `agent/`, `prompts/`,
  `tests/agent/`, and live traces in all four artifact directories; live
  quality is zero and is disclosed above.
- Improvement (15): **verified** — frozen prompt files, `v1-frozen`/
  `v2-frozen`, `evidence/v1-v2-comparison.json`, and diagnosis evidence;
  measured gain was zero.
- API/DB (15): **verified** — `claimlens/api/`, `claimlens/db/`,
  `tests/api/`, and 20 passing API/PostgreSQL tests with `DATABASE_URL`.
- Langfuse (15): **verified** — `claimlens/tracing.py`, four
  `trace_verification.json` files, and `evidence/trace-diagnosis.json`.
- Reliability/tests (15): **verified** — `providers/`, `runner/executor.py`,
  reliability tests, and 57 passing pytest tests.
- Reproducibility (5): **partially verified** — pinned setup, README,
  Dockerfile, hashes, manifest, and handoff are present; Docker build and
  `make run` remain unexecuted.
