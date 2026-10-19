# ClaimLens handoff

## Reviewer entry point

The current final evidence is summarized in:

- `evidence/final-run-summary.json`
- `evidence/v1-v2-final-comparison.json`
- `evidence/submission/final-provenance.json`
- `evidence/audit/current-audit.json`
- `evidence/MANIFEST.json`

Only these four directories feed the final summary and comparison:

```text
artifacts/v1-development-final-turn-gated/
artifacts/v2-development-final/
artifacts/v1-check-final/
artifacts/v2-check-final/
```

Every other `artifacts/*` directory is retained historical or exploratory
evidence.  It is not deleted and is not silently substituted into the final
comparison.

## Exact setup and operation commands

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
cp .env.example .env
# Place answers.csv, brand_facts.csv, and development_gold.csv here,
# or set CLAIMLENS_DATA_DIR in .env.
make test
make verify
make hash
```

For the PostgreSQL-backed API:

```bash
createdb claimlens
set -a; . ./.env; set +a
make run
```

For the local container path:

```bash
docker build --tag claimlens:local .
docker run --rm --env-file .env -p 8000:8000 claimlens:local
```

The assessment requires no hosted deployment.  The Docker commands are the
documented local deployment path.  The recorded session could not complete a
Docker build because the local Docker daemon was not running, and `make run`
was not started.

## Final run accounting

| Run | Run ID | Source revision | Result | Error rows |
|---|---|---|---|---|
| v1 development | `8cb56eec-6c54-4eab-aba2-317bee75d451` | `9de1a5ec5575109b8b395f5645fbc4bdf474c692` (`v1-final-frozen`) | 40 scheduled, 37 ok, 605 predictions, joint F1 `0.660550` | A04/A22/A30 `provider_timeout` |
| v2 development | `5a64e5ff-d1e4-4e49-8175-e2c3617c26cb` | `8293d189f9bae7619a03247acc78aa6fa1f848bc` (`v2-final-frozen`) | 40 scheduled, 37 ok, 610 predictions, joint F1 `0.679513` | A25 empty/trimmed quote, A30/A34 exact-source-quote validation |
| v1 check | `93af5381-5900-43f5-8543-29e32e1ee64f` | `c76941fec1713c00502575f7e09b022c3c4c3975` | 10 scheduled, 9 ok, 143 predictions, quality `null` | A49 exact-source-quote validation |
| v2 check | `a58a7b95-2784-4184-abd5-2161a9386028` | `c76941fec1713c00502575f7e09b022c3c4c3975` | 10 scheduled, 7 ok, 117 predictions, quality `null` | A41/A42/A43 `provider_timeout` |

The development source revisions are frozen separately for v1 and v2.  Both
final checks were run later from the shared check-run revision `c76941f`, which
must not be described as either frozen development source.  Current committed
`HEAD` is `4711df54bcf55ec1ef9ef50ae6cd43ec9cfbef02`, later than all four live
runs.  The current `prompts/v2.txt` hash is
`75971d636ef45536e14025d93f27ca40b3affd3bfe1211f9384158a408761a62`; the v2
development and v2 check artifacts record the frozen-run hash
`74ea421cd3f73788b82326c5f0ea621e61e5905ce9e512501add72550120bcaf`.

## 605 to 610 decomposition

The saved answer-score tables give this exact accepted-prediction change:

```text
A04  +18  provider_timeout -> ok
A22  +17  provider_timeout -> ok
A25  -16  ok -> invalid_output
A34  -15  ok -> invalid_output
A32   +1  ok -> ok
                  +5 net
```

The comparison helper reports 30 fixed and 19 regressed claim IDs.  The
provenance split is retained in `evidence/submission/final-provenance.json`:

- 19 fixed IDs are error-driven A04/A22 recoveries; 11 fixed IDs are genuine
  changes on otherwise successful answers.
- 14 regressed IDs are error-driven A25/A34 misses; 5 regressed IDs are
  genuine changes on otherwise successful answers.
- Two `missed -> wrong_label` and three `wrong_label -> missed` transitions
  caused by those answer errors are listed separately because they are not
  included in the comparison helper's fixed/regressed counters.

The v1 diagnosis found 261 unmatched predictions: 254 advice/instruction-only
sentences, 3 explicit meta-instruction markers, 2 factual-looking supported
sentences, and 2 unsupported/unresolved sentences.  The 254 figure is from
the saved offline analysis; the v2 advice bucket is a separately labelled
text/reason diagnostic and is not a new score.

The v2 change targeted advice/instruction/opinion-only extraction.  Joint F1
rose by `+0.018963`; the paired bootstrap estimate was `+0.015324` with 95% CI
`[-0.046936, +0.083638]`, which includes zero.

## Canaries and Langfuse limits

Both final development injection canary reports are failed outputs.  For each
of A07, A18, and A32, the marker sentence was extracted in v1 and v2.  The
marker did not cause an all-`supported` override (`override_suspected: false`),
but extraction of the marker is itself a canary failure.  These results are
preserved in `evidence/v1-final-injection-canary.json` and
`evidence/v2-final-injection-canary.json`.

Each final artifact has one trace receipt per scheduled answer and non-empty
observation counts in the saved receipt files.  The evidence limitation is
important:

1. `trace_verification.json` and `trace_exports.json` are sanitized local
   summaries, not full remote observation payloads for all 100 traces.
2. The retained Langfuse investigation fetched one selected trace and showed
   authenticated HTTP 200 retrieval, health HTTP 200, and a trace-ID/run-window
   mismatch.  It does not prove independent remote retrieval of every trace.
3. `artifacts/v2-development-final/trace_verification_error.json` records
   `NameError: name 'timedelta' is not defined`.  The 40 receipt rows remain
   present and non-empty; the verifier-process error is disclosed, not hidden.
4. No credentials, model costs, or unavailable remote payloads are fabricated.

## Current checks and exact outputs

Commands run during this documentation refresh:

```text
PYTHONPATH=. .venv/bin/python evidence/audit/regenerate_evidence.py
  exit 0; offline; generated manifest with 223 selected file entries;
  joint_f1_delta=0.018962725868582164

.venv/bin/python evidence/audit/repository_audit.py
  exit 0; required artifact receipts, development comparability, saved
  trace/event diagnostics, and selected manifest hashes complete;
  current-vs-frozen v2 prompt provenance differs as disclosed; the separate
  v2 verifier-process status remains incomplete.

make verify
  exit 0; v1 and v2 saved development metric objects matched deterministic
  rescoring; no provider call.

make test
  exit 0; 99 passed, 1 warning.
```

The full captured outputs are `evidence/pytest-output.txt` and
`evidence/verify-output.txt`; workspace-local absolute paths are redacted to
`<workspace>` for portability.  No source or test file was changed by T4.

## Checklist

- **VERIFIED** — Python version, dependency lockfile, Dockerfile, and
  secret-free environment template are present.
- **VERIFIED** — deterministic evaluator and offline saved-run verification.
- **VERIFIED** — terminal accounting and check-split null quality fields in all
  four designated artifacts.
- **VERIFIED** — selected manifest entries, local CSV hashes, frozen prompt
  hashes, run IDs, and source revisions are actual values.
- **VERIFIED** — v1 diagnosis, v2 comparison, 605→610 decomposition, canary
  failures, and fixed/regressed provenance are retained.
- **VERIFIED** — current full `make test` reports 99 passing tests with one
  deprecation warning; see `evidence/pytest-output.txt`.
- **INCOMPLETE** — v2 development has a saved trace-verification error file
  even though receipt rows are present.
- **UNVERIFIED** — Docker build, live API smoke, hosted deployment, and a
  full reviewer-facing Langfuse export.

## Disclosure and ownership

The assessment inputs remain local ignored files; only hashes are recorded.
Credentials remain in `.env` and are never written to this handoff.  OmniRush.ai
and delegated coding agents assisted with implementation and test writing.  T4
ran only offline audit, manifest, summary, comparison, verification, and test
commands; it did not run provider experiments or spend API credits.  T4 changed
only the documentation/evidence paths assigned to it and did not commit.
