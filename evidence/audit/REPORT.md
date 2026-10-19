# Assessment audit

Audit date: 2026-10-19. Scope is the current worktree, `HEAD` commit
`9de1a5e` (`fix: serialize post-tool completion turns`), the two frozen tags,
and the saved artifacts. No provider, database, Docker, or Langfuse experiment
was started by this audit.

The reproducible checker is `evidence/audit/repository_audit.py`. It reads the
four designated run directories, validates terminal accounting and check-split
redaction, compares source revisions and prompt hashes, and reports the later
unverified candidate artifact.

## Status key

- **Complete**: implementation and/or saved evidence directly supports the
  requirement.
- **Incomplete**: a required part is absent, inconsistent, or not controlled
  by the submitted evidence.
- **Unverified**: the repository describes or implements the behavior, but the
  required live/environment check was not run or is not retained.

## Requirement audit

| Area | Status | Finding and evidence |
|---|---|---|
| FastAPI `POST /classify` | Complete structurally; runtime unverified | Strict request model and `v1`/`v2` validation are in `claimlens/api/app.py:40-61`; the route is at `:524-535`. Error mapping covers 422, 404, 502, and 504 with sanitized bodies (`:112-149`). `tests/api/test_app.py` and `tests/api/test_api.py` exercise success, invalid input, unknown brand, provider failure, invalid output, and timeout. `make run` was not started, so the default live dependency path remains unverified. |
| FastAPI evaluation endpoints | Complete structurally; runtime unverified | `POST /evaluation-runs` and `GET /evaluation-runs/{run_id}` are implemented at `claimlens/api/app.py:537-668`, with UUID validation, stable answer/span ordering, check-metric redaction, and repository persistence hooks. Tests cover fake runner/retrieval behavior. A live server request was not run in this audit; the handoff also records that `make run` was not run. |
| PostgreSQL persistence | Complete structurally and covered by the full test run; standalone receipt unverified | `claimlens/db/schema.py:13-77` defines `runs`, `answer_results`, and `claim_results`, foreign keys, status/label checks, and the required unique constraints. `claimlens/db/repository.py:150-219,242-345,357-454` uses parameterized SQL and joins rows deterministically. Existing integration tests cover an ok claim, an error row, an empty result, and retrieval; `HANDOFF.md:60-62` records 20 API/PostgreSQL tests with `DATABASE_URL`. The shell starts without that variable, but the imported tracing module loads the ignored `.env`; the full `make test` run passed without skips. No separate DB-only output was retained by this audit. |
| Terminal answer/error accounting | Complete | `runner/executor.py:210-225` returns one outcome per input and sorts by `answer_id`; `harness/evaluator.py:97-154` rejects missing, duplicate, and extra outcomes. The four designated artifacts each have one outcome per scheduled answer, including errors and empty claims. The repository schema enforces one `(run_id, answer_id)` row. |
| Exact span, quote, label, fact-ID and overlap validation | Complete | `agent/core.py:257-359` validates fields, labels, fact IDs, exact quotes, localized Unicode offsets, sorted non-overlapping spans, and two-sentence reasons. Tests cover malformed JSON, labels, invented fact IDs, overlap, Unicode offsets, repeated/wrong offsets, and answer-level error behavior. |
| Three-turn limit | Complete | `agent/core.py:41-45,430-435,620-680` limits turns to three and converts exhaustion into an answer error with no claims. `tests/agent/test_core.py::test_turn_exhaustion_never_makes_a_prediction` verifies this. |
| Tool name/argument/brand validation | Complete | `_tool_schema` and `_parse_tool_arguments` are in `agent/core.py:361-375,183-196`; `_execute_tool` rejects illegal names, unknown brands, malformed bundles, and cross-brand fact rows (`:575-599`). Existing tests cover illegal tools, unknown brands, multiple tool calls, and invented fact IDs. Designated live exchanges contain `get_brand_facts` observations. |
| Gold isolation | Complete structurally | The agent imports contracts and prompts but not `harness` or `development_gold`; `tests/agent/test_import_isolation.py` checks that graph. `runner/experiment.py:288-314` evaluates outcomes first and loads development gold afterward. Injection canaries and the audit test also check the boundary. The run metadata includes a SHA-256 of `development_gold.csv`, which is provenance metadata rather than labels/spans supplied to the provider. |
| Deterministic harness and metrics | Complete | `harness/evaluator.py` implements exact span/quote matching, extraction and joint precision/recall/F1, coverage, per-label metrics, macro F1, confusion matrix, missed/extra/wrong IDs, and answer-error counts. `tests/harness/test_evaluator.py` covers perfect output, misses, extra advice, wrong labels, provider errors, quote equality, outcome identity, the required `G=10,P=10,M=8,C=7` arithmetic, confusion-matrix invariants, and null empty-split quality. |
| CSV parsing and typed facts | Complete after audit addendum | `claimlens/loaders.py:47-81` uses `csv.DictReader`, UTF-8, `newline=""`, exact answer text, and typed booleans/numbers. The new audit test directly verifies a quoted comma, embedded newline, quote preservation, and `false` becoming `False`. This check was absent from the pre-existing test directories. |
| Concurrency ceiling | Complete implementation; retry-combination coverage incomplete | `runner/executor.py:23-64,102-119` sets a process-wide logical ceiling of three active provider attempts and holds a permit for each retry attempt. `tests/reliability/test_executor.py` verifies `max_active <= 3` for concurrent answers, but does not combine concurrency pressure with retries. |
| 15-second provider timeout | Complete | `runner/executor.py:23-25,73-119`, `agent/core.py:43-45,490-543`, and `providers/openai.py:23,48-57,95-112` all use a 15-second default/per-attempt timeout. The provider request-shape test asserts `timeout: 15.0`; all four designated run metadata records contain `request_timeout_seconds: 15.0`. |
| Selective retry policy | Complete implementation; rate-limit end-to-end coverage incomplete | Timeout, `ProviderTransientError`, and status 429 are retryable; auth errors and malformed output are not (`agent/core.py:406-413,500-572`). Existing tests cover transient retry, auth non-retry, malformed-output non-retry, and timeout. The new audit test covers 429 classification, but no test drives a full provider 429 response through two attempts. |
| Four required live-run artifact directories | Complete core receipts | All four directories contain `run.json`, `config.json`, `outcomes.json`, `predictions.json`, `report.json`, `timings.json`, `exchanges.jsonl`, `trace_verification.json`, and `trace_exports.json`. The checker found one terminal outcome per scheduled answer and non-empty verification observations for every listed trace. See the table below. |
| Development scores and comparison | Incomplete as a controlled v1/v2 experiment | The saved comparison is deterministic and reports v1/v2 development `joint_f1=0.0`, but the designated v1 development artifact was produced at `da37f15`, while v2 was produced at `a9ce77e`. The `a9ce77e` diff changes both the v2 prompt and `_bundle_payload` serialization in `agent/core.py`; this is not a prompt-only controlled change. The v1/v2 frozen tags both point to `a9ce77e`, so the v1 development run is not from the frozen source used for v2. |
| Final-check isolation | Complete for designated artifacts | `v1-check` and `v2-check` each schedule A41-A50 and their reports keep `gold_claims`, matches, correctness, and all quality metrics null. They retain outcomes and predictions (zero accepted predictions in these saved runs). |
| Langfuse actual trace evidence | Partial/incomplete | The four designated receipts contain 40/40/10/10 non-empty observation receipts, and local exchanges include generation and `get_brand_facts` events. However, those exchanges predate `provider_attempt` and `answer_diagnostics` recording, so the audit checker reports missing per-attempt diagnostics. The retained `evidence/trace-diagnosis.json` is a sanitized diagnosis for one trace; `evidence/langfuse-investigation/report.json` confirms only one selected remote trace was fetched. No reviewer-facing full export, screenshot, or link for all four runs is retained. |
| Version/hash metadata | Incomplete in current worktree; artifact metadata complete | Designated runs include dataset, fact, evaluator, prompt, provider/model/settings, source revision, CSV hashes, and trace IDs. `evidence/MANIFEST.json` has exact CSV hashes and frozen prompt hashes, but records commit `e9a557a` rather than current `HEAD 9de1a5e`; current `prompts/v2.txt` hashes to `74ea421c...` while the manifest records `1ebbc6b8...`. The current worktree has 99 status entries, including untracked later artifacts and this audit addendum. |
| Documentation and reproducibility | Partial/unverified | `.python-version`, `requirements.lock`, `Dockerfile`, `.env.example`, `README.md`, and `HANDOFF.md` are present. README has setup/run/test commands and PostgreSQL setup, but no explicit deploy command. `HANDOFF.md:62-67` records that Docker build was blocked by a stopped local daemon and `make run` was not run. Therefore the clean Docker build/run path and documented deploy path remain unverified. |

## Designated artifact accounting

These are the four artifacts named by `HANDOFF.md`, not later exploratory
directories:

| Artifact | Source revision | Scheduled | OK | Errors | Predictions | Trace receipts |
|---|---:|---:|---:|---:|---:|---:|
| `artifacts/v1-development-rerun` | `da37f15` | 40 | 0 | 40 | 0 | 40/40 non-empty |
| `artifacts/v2-development` | `a9ce77e` | 40 | 0 | 40 | 0 | 40/40 non-empty |
| `artifacts/v1-check` | `a9ce77e` | 10 | 6 | 4 | 0 | 10/10 non-empty |
| `artifacts/v2-check` | `a9ce77e` | 10 | 2 | 8 | 0 | 10/10 non-empty |

The zero-prediction development result is a real observed provider-timeout
outcome, not a scorer zeroing or a missing-outcome shortcut. The reports retain
376 gold claims, 376 missed IDs, and 40 answer errors for each development
artifact. The check reports correctly expose no private quality score.

There is also an untracked later artifact,
`artifacts/v1-development-final-turn-gated`, with 605 predictions and
`joint_f1=0.6605504587155964`, but it has no `trace_verification.json` and is
not included in `evidence/MANIFEST.json` or the handoff's four-run comparison.
It must not be silently substituted for the designated v1 development run.

## Commit and worktree findings

Relevant commits/tags:

```text
9de1a5e (HEAD) fix: serialize post-tool completion turns
a9ce77e (v1-frozen, v2-frozen) feat: tighten v2 extraction and compact fact payloads
e9a557a docs: record final reproducibility limits   # manifest commit
```

The worktree was already dirty before this audit (`prompts/v2.txt`, the
assessment input, later artifacts/evidence, and other files). This worker added
only the two files listed below and did not commit:

```text
evidence/audit/repository_audit.py
tests/audit/test_repository_audit.py
```

## Commands run and real outputs

All commands were offline and used the project virtual environment.

```text
$ .venv/bin/python -m compileall -q evidence/audit tests/audit
# exit 0; no output

$ .venv/bin/python -m pytest -q tests/audit
......                                                                   [100%]
6 passed in 0.31s
# exit 0; one existing pytest-asyncio fixture-loop-scope deprecation warning

$ make test
.venv/bin/python -m pytest -q
..................................................................       [100%]
66 passed, 1 warning in 12.09s
# exit 0; the warning is from Starlette/pytest-asyncio configuration

$ .venv/bin/python evidence/audit/repository_audit.py
required_artifact_receipts: complete
trace_diagnostics: incomplete
development_comparability: incomplete
manifest_matches_worktree: incomplete
late_candidate_receipt: unverified
# exit 0
```

The audit script and focused audit tests did not launch provider experiments,
start a server, build Docker, or call Langfuse. The broader `make test` run did
exercise the repository's configured PostgreSQL integration through the
project's dotenv loading; it did not make provider calls.

## Blockers to a fully verified submission

1. Re-run or replace the v1 development artifact at the same frozen source as
   v2, with the exact intended one-variable change; then regenerate the
   comparison and manifest.
2. Decide whether the later `v1-development-final-turn-gated` run is evidence;
   if retained, add its trace verification receipt and include it consistently,
   rather than mixing it with the older four-run set.
3. Preserve per-attempt diagnostics/full sanitized trace exports for the four
   designated runs, or explicitly scope the evidence to the retained summary
   receipts.
4. Run the documented PostgreSQL API tests with `DATABASE_URL`, run the API
   process smoke check, and complete a Docker build/run on a live daemon.
5. Update the reproducibility manifest/documentation so the current commit,
   prompt hash, artifact set, and deploy instructions agree.
