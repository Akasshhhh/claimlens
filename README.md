# ClaimLens

ClaimLens extracts complete factual sentences from long product answers,
looks up catalogue facts through a real `get_brand_facts` tool call, and labels
each extracted sentence `supported`, `contradicted`, or `unknown`.  The
repository also contains the deterministic evaluator, bounded async runner,
FastAPI/PostgreSQL layer, Langfuse tracing, saved run artifacts, and offline
evidence checks.

## Repository map

```text
answers.csv + brand_facts.csv ─> CSV loaders ─> ClaimAgent
                                             ├─ get_brand_facts tool call
                                             ├─ exact Unicode span validation
                                             └─ Langfuse trace

OpenAIProvider -> bounded executor -> AnswerOutcome
                                      ├─ deterministic harness reports
                                      ├─ PostgreSQL run/answer/claim rows
                                      └─ immutable artifacts/<run>/
```

- `agent/`: tool loop, claim validation, retries, and answer-level errors.
- `prompts/`: v1 baseline and v2 candidate prompt files.
- `providers/`: official OpenAI SDK adapter.
- `runner/`: bounded execution and immutable run artifacts.
- `harness/`: exact-span matching, metrics, comparisons, and canaries.
- `claimlens/api/`: FastAPI routes.
- `claimlens/db/`: PostgreSQL schema, repository, and artifact import.
- `evidence/`: selected manifests, analysis, audit output, and submission notes.

## Requirements and setup

Python `3.12.7` is pinned in `.python-version`; dependency versions are pinned
in `requirements.lock`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
```

Put the three supplied CSV inputs in the repository root, or set
`CLAIMLENS_DATA_DIR` to their directory:

- `answers.csv`
- `brand_facts.csv`
- `development_gold.csv`

The local assessment inputs are ignored by Git.  Their SHA-256 values are
recorded in `evidence/MANIFEST.json`; the manifest explicitly covers selected
evidence, not the complete repository tree.

Set these values in the ignored `.env` for live operation:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
DATABASE_URL=postgresql://claimlens:claimlens@localhost:5432/claimlens
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

No API call is needed for the tests, verification, or audit commands below.

## Run locally

The service uses PostgreSQL; SQLite is not supported.

```bash
createdb claimlens
set -a; . ./.env; set +a
make run
```

The API listens on `http://127.0.0.1:8000`:

```bash
curl -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"answer_id":"A01","brand_id":"cleartrail","question":"Which facts?","ai_answer":"ClearTrail Mini costs INR 1,299 in India.","agent_version":"v1"}'
```

Routes are:

- `POST /classify`
- `POST /evaluation-runs` with `agent_version` (`v1` or `v2`) and `split`
  (`development` or `check`)
- `GET /evaluation-runs/{run_id}`

## Docker build and local deployment

The Dockerfile pins the Python base image and installs the locked dependencies:

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

The assessment requires no hosted deployment.  The Docker commands are the
local deployment path; they require a running Docker daemon and a PostgreSQL
endpoint reachable from the container.  A Docker build was not completed in
the recorded session because the local Docker daemon was stopped.

## Offline tests, verification, and audit

```bash
make test
make verify
make hash
PYTHONPATH=. .venv/bin/python evidence/audit/regenerate_evidence.py
.venv/bin/python evidence/audit/repository_audit.py
```

`make verify` rescored the two saved development reports without calling a
provider and returned exit 0.  The evidence regeneration script only reads
saved JSON/CSV/Git state and rewrites the selected summary, comparison,
provenance, and manifest files.  The repository audit is read-only with
respect to source and run artifacts.

### Current recorded checks

These results describe the worktree at the documentation refresh, including
later uncommitted source changes owned by other tasks:

| Command | Result | Evidence |
|---|---|---|
| `make test` | **VERIFIED** — 99 passed, 1 warning | `evidence/pytest-output.txt` |
| `make verify` | **VERIFIED** — exit 0; both saved development metric objects matched deterministic rescoring | `evidence/verify-output.txt` |
| `repository_audit.py` | **VERIFIED** for selected artifact accounting, source-freeze comparability, saved trace/event receipts, and manifest hashes; the v2 development verifier-process error and current-vs-frozen prompt provenance remain separately disclosed | `evidence/audit/current-audit.json` |
| Docker build / `make run` smoke | **UNVERIFIED** in the recorded session; Docker daemon was unavailable and the API process was not started | `HANDOFF.md` |

The captured test result is retained; workspace-local paths in the transcript
are redacted to `<workspace>` for portability.  This documentation task did
not alter source or tests to change the result.

## Final live-run results

The four designated final directories are the only runs used by
`evidence/final-run-summary.json` and
`evidence/v1-v2-final-comparison.json`.  Earlier directories remain in
`artifacts/` for historical auditability and are not silently substituted.

| Run | Run ID | Run source revision | Answers | Errors | Predictions | Joint F1 |
|---|---|---|---:|---:|---:|---:|
| v1 development | `8cb56eec-6c54-4eab-aba2-317bee75d451` | `9de1a5e` (`v1-final-frozen`) | 37/40 ok | 3 | 605 | 0.660550 |
| v2 development | `5a64e5ff-d1e4-4e49-8175-e2c3617c26cb` | `8293d18` (`v2-final-frozen`) | 37/40 ok | 3 | 610 | 0.679513 |
| v1 check | `93af5381-5900-43f5-8543-29e32e1ee64f` | `c76941f` (later check-run source) | 9/10 ok | 1 | 143 | null |
| v2 check | `a58a7b95-2784-4184-abd5-2161a9386028` | `c76941f` (later check-run source) | 7/10 ok | 3 | 117 | null |

Development metrics:

| Metric | v1 | v2 |
|---|---:|---:|
| Extraction F1 | 0.701325 | 0.705882 |
| Joint F1 | 0.660550 | 0.679513 |
| Macro label F1 | 0.743466 | 0.769791 |
| Correct-label coverage | 0.861702 | 0.890957 |

The v1 development errors were `A04`, `A22`, and `A30`, all
`provider_timeout`.  The v2 development errors were `A25`
(`quote must be non-empty and trimmed`), `A30` (`quote does not occur exactly
in source answer`), and `A34` (the same exact-quote validation error).  The v1
check error was `A49` (`quote does not occur exactly in source answer`).  The
v2 check errors were `A41`, `A42`, and `A43`, all `provider_timeout`.

Check reports intentionally keep quality metrics, gold claims, matches,
correctness, and missing/extra/wrong-label IDs null.  They are not zero scores.

### What changed from 605 to 610

The net `+5` accepted-prediction change decomposes exactly as:

```text
A04  +18  v1 provider_timeout -> v2 ok
A22  +17  v1 provider_timeout -> v2 ok
A25  -16  v1 ok -> v2 invalid_output
A34  -15  v1 ok -> v2 invalid_output
A32   +1  v1 ok -> v2 ok
                 ----------------
                  +5 total
```

The full answer-level rows and claim-ID decomposition are in
`evidence/submission/final-provenance.json`.  Among the 30 comparison-table
fixed IDs, 19 are error-driven recoveries from A04/A22 and 11 are genuine
successful-answer changes.  Among the 19 comparison-table regressed IDs, 14
are error-driven misses from A25/A34 and 5 are genuine changes in answers that
remained successful.  Two missed-to-wrong-label and three wrong-label-to-missed
changes caused by those answer errors are recorded separately because the
comparison helper does not count them as fixed/regressed.

## Diagnosis, improvement, and canaries

The v1 offline diagnosis found 261 unmatched predictions: 254
advice/instruction-only sentences, 3 explicit meta-instruction markers, 2
other factual-looking supported sentences, and 2 unsupported/unresolved
sentences.  The intended v2 change was to exclude advice, instructions, and
opinion-only sentences that make no verifiable factual assertion.  A saved
text/reason bucket over v2's unmatched rows is reported separately in
`final-provenance.json`; it is diagnostic, not a new gold metric.

The measured joint-F1 delta is `+0.018963`.  The paired bootstrap estimate is
`+0.015324`, with 95% CI `[-0.046936, +0.083638]`; the interval includes zero.
The comparison therefore records an observed improvement without claiming a
conclusive generalization gain.

Both final development injection canaries **failed**:
`A07`, `A18`, and `A32` each had the marker sentence extracted in both v1 and
v2.  `override_suspected` was false and not all labels were `supported`, but
marker extraction itself makes each canary fail.  See
`evidence/v1-final-injection-canary.json`,
`evidence/v2-final-injection-canary.json`, and the structured submission
summary.

## Source and trace provenance

The development source revisions are intentionally distinct frozen revisions:

- v1 development: `9de1a5ec5575109b8b395f5645fbc4bdf474c692`
- v2 development: `8293d189f9bae7619a03247acc78aa6fa1f848bc`
- both final checks: `c76941fec1713c00502575f7e09b022c3c4c3975` (a later
  check-run revision used for the trace-verification window fix)
- current committed `HEAD`: `4711df54bcf55ec1ef9ef50ae6cd43ec9cfbef02`

The v2 development artifact used prompt hash
`74ea421cd3f73788b82326c5f0ea621e61e5905ce9e512501add72550120bcaf`.  The
current committed `prompts/v2.txt` has hash
`75971d636ef45536e14025d93f27ca40b3affd3bfe1211f9384158a408761a62` after a
later imperative-wording clarification.  The manifest records both hashes;
the saved run is not rewritten to imply a replay from current HEAD.

The final artifact directories contain one terminal answer outcome per
scheduled answer, local generation/tool/provider-attempt diagnostics, and
non-empty saved trace-observation receipts.  Langfuse limitations are:

- the verifier receipt is a local summary (`trace_id`, observation count, and
  names), not a full reviewer-facing export of every remote observation;
- the retained investigation fetched one selected trace and showed health
  HTTP 200, authenticated observation retrieval, and the run-window/trace-ID
  query mismatch; it does not prove independent remote retrieval of all 100
  traces;
- `v2-development-final/trace_verification_error.json` records a
  `NameError: name 'timedelta' is not defined`, while its 40 non-empty receipt
  rows remain saved; this is disclosed as an incomplete verifier process, not
  erased;
- trace payloads, API keys, and model costs are not fabricated or committed.

## Evidence checklist

- **VERIFIED** — pinned Python/dependencies and secret-free `.env.example`.
- **VERIFIED** — deterministic evaluator, exact-span rules, development
  comparison, and offline `make verify` metric match.
- **VERIFIED** — four saved final artifact directories with terminal accounting
  and check-split gold-score nulling.
- **VERIFIED** — selected manifest entries and CSV/prompt hashes match the
  actual files covered by the manifest.
- **VERIFIED** — advice diagnosis, 605→610 decomposition, fixed/regressed
  provenance, canary failures, and source revisions are retained in evidence.
- **VERIFIED** — current full `make test` reports 99 passing tests with one
  deprecation warning; see `evidence/pytest-output.txt`.
- **INCOMPLETE** — one saved v2 trace-verification error file exists despite
  complete local receipt rows.
- **UNVERIFIED** — Docker build, live `make run` smoke, hosted deployment, and
  full reviewer-facing Langfuse export.

## Security and disclosure

Keep credentials only in the ignored `.env`.  Do not commit API keys, database
passwords, Langfuse secrets, virtual environments, caches, or temporary
provider output.  The assessment inputs are local ignored files; only their
hashes are recorded.  OmniRush.ai and delegated coding agents assisted with
implementation and test writing.  This documentation refresh ran only offline
manifest, summary, comparison, audit, test, and verification commands and did
not run provider experiments or spend API credits.
