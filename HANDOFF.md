# ClaimLens handoff

## Current submission set

The shared agent/validation fixes invalidated the prior controlled experiment,
so four new final runs were produced. All prior frozen-tag and failed artifacts
remain in `artifacts/`; none were overwritten.

| Run | Run ID | Source | Result | Errors |
|---|---|---|---|---|
| v1 development | `1d30770a-933b-4982-83be-875d24c02682` | `a0020d0` | 40 scheduled, 22 ok, 201 predictions, joint F1 `0.665511` | 18 provider timeouts |
| v2 development | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | `a0020d0` | 40 scheduled, 26 ok, 231 predictions, joint F1 `0.714992` | 13 provider timeouts; one invalid output |
| v1 check | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | `a0020d0` | 10 scheduled, 5 ok, 44 predictions, quality `null` | A43/A44/A46/A48/A50 timeouts |
| v2 check | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | `a0020d0` | 10 scheduled, 9 ok, 83 predictions, quality `null` | A46 timeout |

Final artifact locations:

```text
artifacts/v1-development-corrected-final/
artifacts/v2-development-corrected-final/
artifacts/v1-check-corrected-final/
artifacts/v2-check-corrected-final/
```

The corrected v1 and v2 development runs use identical post-fix source,
dataset, evaluator, provider, model, timeout, retry, turn, and concurrency
settings. The sole model-facing behavior change is the v2 prompt policy for
excluding pure advice, opinions, instructions, and injected instructions.

## Exact commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
make test
make verify
make hash
PYTHONPATH=. .venv/bin/python evidence/audit/regenerate_evidence.py
PYTHONPATH=. .venv/bin/python evidence/audit/repository_audit.py
```

PostgreSQL API tests:

```bash
set -a; . ./.env; set +a
.venv/bin/pytest -q tests/integration/test_api_postgres.py tests/api/test_api.py tests/api/test_app.py
```

API operation:

```bash
createdb claimlens
set -a; . ./.env; set +a
make run
```

Docker:

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

## Verification receipts

Recorded after the corrected artifacts were created:

- `make test`: **102 passed, 1 warning**;
- `make verify`: exit 0, both corrected development metric objects matched;
- API/PostgreSQL tests with the running Docker PostgreSQL container:
  **24 passed, 1 warning**;
- focused agent/reliability/Langfuse/API/integration gate: **100 passed, 1
  warning**;
- Docker build: passed;
- Docker container test suite without `DATABASE_URL`: **97 passed, 5 skipped**;
- Docker container integration/API suite with PostgreSQL via
  `host.docker.internal`: **10 passed, 1 warning**.

Raw current command receipts are in:

- `evidence/pytest-output.txt`
- `evidence/verify-output.txt`
- `evidence/final-run-summary.json`
- `evidence/submission/final-provenance.json`
- `evidence/audit/current-audit.json`
- `evidence/MANIFEST.json`

## Assessment behavior and reliability

Offline tests now prove:

- genuine async overlap and a hard maximum of three active provider attempts;
- retry attempts share that global ceiling;
- OpenAI-compatible HTTP 429 retries once end-to-end;
- authentication and malformed-output failures are not retried;
- 15-second request timeout, maximum two attempts per turn, and maximum three
  model turns;
- duplicate raw/canonical IDs, invalid bounds, quote mismatch, overlap,
  Unicode offsets, and reason normalization;
- provider messages and tool arguments exclude gold labels, spans,
  explanations, difficulty tags, and scores;
- pure advice/opinion/instruction/meta-instruction filtering while an
  independent factual clause remains extractable.

The exact tests are in `tests/agent/`, `tests/reliability/`, and
`tests/langfuse/`.

## API and PostgreSQL evidence

The real PostgreSQL integration covers all three routes through the actual
FastAPI application, stable joined retrieval, sanitized errors, UUID handling,
empty and error answer rows, claim rows and development match fields. It also
checks PostgreSQL uniqueness and foreign-key rejection in the integration
coverage. SQL remains parameterized and existing data is not deleted.

The tests use a Docker Desktop PostgreSQL container exposed at
`127.0.0.1:55433` for the host run. No test database data is part of the
repository deliverable.

`make run` itself was not left running as a background process; a live process
smoke remains a separate **INCOMPLETE** item. TestClient integration is not
being misrepresented as a deployed service.

## Langfuse evidence

`claimlens.tracing` now constructs explicit UTC run windows and
`runner/experiment.py` verifies per-answer trace IDs without allowing a
verification failure to erase completed provider outcomes. The historical
v2-development `timedelta` error is preserved in its old artifact; corrected
final runs have no verifier-error file.

The read-only auditor is:

```bash
PYTHONPATH=. .venv/bin/python evidence/langfuse-investigation/audit.py --offline
```

The final read-only remote audit queried all 100 corrected trace IDs and found
86 non-empty observation responses plus 14 HTTP 429 responses. The 429s are
recorded rate-limit blockers, not treated as missing ingestion. The tool records
only sanitized status/count/name/digest results; it does not export raw prompts,
model outputs, tool payloads, scores, credentials, or private Langfuse data.
Full reviewer-facing exports remain **INCOMPLETE**.

## Metrics and interpretation

| Metric | v1 | v2 | Delta |
|---|---:|---:|---:|
| Extraction F1 | 0.696707 | 0.761120 | +0.064413 |
| Joint F1 | 0.665511 | 0.714992 | +0.049480 |
| Macro label F1 | 0.665583 | 0.714444 | +0.048861 |
| Correct-label coverage | 0.510638 | 0.577128 | +0.066489 |
| Predictions | 201 | 231 | +30 |
| Matched spans | 201 | 231 | +30 |

The corrected reports have zero unmatched accepted prediction IDs in both
development runs. This is the evidence-based result of the new deterministic
filter plus the live runs; the old historical advice counts and failed canary
outputs remain available for comparison.

The paired bootstrap estimate and 95% interval are in
`evidence/v1-v2-final-comparison.json`. The interval must be read before
claiming generalization. No check labels were loaded or inspected.

## Status checklist

| Requirement | Status | Evidence / remaining action |
|---|---|---|
| Concurrency ceiling and overlap | **VERIFIED** | `tests/reliability/test_executor.py`; max active exactly 3 |
| Selective retries including 429 | **VERIFIED** | `tests/reliability/test_openai_provider.py`, `tests/reliability/test_executor.py` |
| Gold isolation | **VERIFIED** | `tests/agent/test_core.py`; captured provider/tool payload assertions |
| Claim validation | **VERIFIED** | duplicate IDs, bounds, Unicode, quote, overlap, reason tests |
| Corrected v2 behavior | **VERIFIED** for representative offline/live evidence | corrected reports have zero unmatched predictions; historical canaries remain failed evidence |
| Live API process | **INCOMPLETE** | actual app + real DB TestClient integration passed; `make run` smoke not retained |
| PostgreSQL constraints/persistence | **VERIFIED** | host and Docker integration tests passed |
| Offline verification | **VERIFIED** | current corrected artifacts, `make verify`, manifest |
| Langfuse | **INCOMPLETE** | verifier fixed and local receipts complete; all-remote retrieval/full exports not retained |
| Documentation/manifests | **VERIFIED** after regeneration | current selected manifest has matching hashes; evidence scope is explicitly selected, not full-tree |

## Disclosure

OmniRush.ai and delegated coding agents assisted with implementation and test
writing. Credentials remain only in the ignored `.env`; local CSV inputs remain
ignored and only their hashes are recorded. Historical and superseded evidence
is preserved.
