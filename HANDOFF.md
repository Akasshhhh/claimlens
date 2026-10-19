# ClaimLens handoff

## Result

ClaimLens is implemented on branch `wip/cli-handoff`. The repository contains
the shared contracts/loaders, live OpenAI tool-calling agent, bounded async
executor, deterministic evaluator, FastAPI/PostgreSQL layer, Langfuse tracer,
runner, immutable JSON artifacts, and offline verification command.

The original failed runs remain preserved. After offline diagnosis, a new
controlled sequence was executed with v1 held at `v1-final-frozen` and v2 held
at `v2-final-frozen`:

| run | run ID | evidence | result |
|---|---|---|---|
| v1 development A01–A40 | `8cb56eec-6c54-4eab-aba2-317bee75d451` | `artifacts/v1-development-final-turn-gated/` | 37 ok, 3 timeout errors, 605 predictions; joint F1 0.660550 |
| v2 development A01–A40 | `5a64e5ff-d1e4-4e49-8175-e2c3617c26cb` | `artifacts/v2-development-final/` | 37 ok, 3 timeout errors, 610 predictions; joint F1 0.679513 |
| v1 check A41–A50 | `93af5381-5900-43f5-8543-29e32e1ee64f` | `artifacts/v1-check-final/` | 9 ok, 1 error, 143 predictions; quality metrics null |
| v2 check A41–A50 | `a58a7b95-2784-4186-aeab-d5-2161a9386028` | `artifacts/v2-check-final/` | 7 ok, 3 errors, 117 predictions; quality metrics null |

The required escalation was also run before the full experiments:
`artifacts/v1-escalation-3/` contains A01–A03 with 2 successful empty outputs
and one invalid-output error. Earlier failed infrastructure attempts remain
as evidence and are not presented as successful runs.

## Diagnosis and targeted change

The offline v1 analysis is in
`evidence/analysis/v1-development-final-turn-gated-analysis.json`. It found
261 unmatched predictions, of which 254 were advice/instruction-only spans;
the three answer errors were A04, A22 and A30 post-tool provider timeouts.
The exact v1 baseline was extraction F1 `0.701325`, joint F1 `0.660550`,
macro F1 `0.743466`, with 605 predictions and 344 matched spans.

Hypothesis: explicitly exclude advice, instructions and opinions that make no
verifiable factual assertion, even when they mention a product or purchase.
That is the only intended v2 model-behavior change; the frozen v1 prompt is
unchanged. Shared pipeline fixes (minimal reasoning, 2000 completion-token
cap, compact tool messages, Python quote localization, reason normalization,
and serialized post-tool turns) are recorded in the run settings and commit
history.

The v2 result was extraction F1 `0.705882`, joint F1 `0.679513`, macro F1
`0.769791`, with 610 predictions and 348 matched spans. Joint-F1 delta was
`+0.018963`; paired bootstrap estimate `+0.015324`, 95% CI
`[-0.046936, +0.083638]`. The controlled comparison is in
`evidence/v1-v2-final-comparison.json`; 30 claims were fixed and 19 regressed.

The v1/v2 comparison, per-answer deltas, per-label regressions, difficulty-tag
recall, claim flip table, and bootstrap result are in
The development-only canaries for A07, A18, and A32 are in
`evidence/v1-final-injection-canary.json` and
`evidence/v2-final-injection-canary.json`.

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

`make test` was run and passed: **66 passed, 1 warning**. `make verify` was
run after the corrected live development artifacts and re-scored both saved
development runs without API calls; both stored metric objects matched
exactly. `make hash`
was run and produced the CSV hashes recorded in `evidence/MANIFEST.json`.

The API uses PostgreSQL only. With the configured `DATABASE_URL`,
`set -a; . ./.env; set +a; .venv/bin/pytest -q tests/api` passed **20 tests**.
No SQLite substitute was used. `make run` was not run in this session. A
Docker build was attempted with Docker 27.5.1 but could not start because the
local Docker daemon was not running (`Cannot connect to the Docker daemon at
unix:///Users/akash/.docker/run/docker.sock`). The Dockerfile and API target
are present; rerun `docker build --tag claimlens:local .` after starting Docker
Desktop.

## Langfuse verification

The Langfuse health check returned HTTP 200 with version `4.63.0`.
Trace verification uses the supported
`GET /api/public/v2/observations` endpoint with a trace ID, a small limit, and
minimal fields. It polls every 5 seconds for up to 1200 seconds with a
45-second request timeout and uses the actual run time window. Empty 200
responses, timeouts, and 5xx responses remain pending, while other 4xx
responses are reported as permanent failures. Ingestion is asynchronous; a
verification timeout is recorded separately and never changes a completed run
to failed. The v2 development and both final-check runs have 40/10/10 fetched
trace receipts; the v1 development receipts were recovered with the same
fixed-window verifier.

All four final run artifact directories contain `trace_verification.json` with
successful observation counts. A sanitized trace diagnosis is in
`evidence/trace-diagnosis.json`.

## Reproducibility and limitations

- Runtime is pinned by `.python-version`; dependencies are pinned in
  `requirements.lock`; system build instructions are in `Dockerfile`.
- The supplied CSVs, `.env`, and credentials remain uncommitted. Only their
  SHA-256 hashes are recorded.
- The exact selected provider/model settings in the corrected runs are OpenAI,
  `gpt-5-mini-2025-08-07`, `reasoning_effort=minimal`,
  `max_completion_tokens=2000`, no temperature, and a 15-second per-attempt
  timeout.
- Development and check inputs are visible to this local operator. No claim is
  made that the final-check benchmark is blind.
- The original zero-prediction runs remain as failed evidence. The corrected
  v1/v2 runs produced accepted predictions and their measured scores are
  reported above without replacing the original artifacts.
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
  Dockerfile, hashes, manifest, and handoff are present; Docker build was
  blocked by the stopped local daemon and `make run` remains unexecuted.
