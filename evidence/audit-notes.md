# T3 evidence audit notes

Date of this audit: 2026-10-20 (Asia/Calcutta). This note records only
evidence that was read from the repository, saved artifacts, Git history, and
the no-credentials local HTTP smoke. No source code, tests, timeout values, or
retry behavior were changed. No provider or model call was made.

## Status at a glance

### Verified

- `agent/core.py` has no executable dependency on gold rows, development
  labels, answer files, or evaluation data. The filter is deterministic text
  logic over the returned quote/reason strings.
- The four corrected final artifact directories exist and each contains
  `config.json`, `run.json`, `outcomes.json`, `predictions.json`, `report.json`,
  `exchanges.jsonl`, and trace receipts. The report files retain predictions,
  outcomes, metrics, and (for development) missing/unmatched/wrong-label IDs.
- Four payload-free exchange summaries were derived from those saved
  `exchanges.jsonl` files under `evidence/exchanges/`. The complete source
  exchanges remain untouched under `artifacts/`.
- `evidence/supplementary/current-four-run-inventory.json` records exact source
  paths, byte counts, SHA-256 digests, settings, outcome counts, exchange event
  counts, and the report metrics copied/derived from the current artifacts.
- The real FastAPI application started with Uvicorn and served all three
  endpoint paths using validation/configuration blockers only. Receipt:
  `evidence/run-smoke.txt`.

### Incomplete or intentionally unverified

- A successful valid `POST /classify` was not sent. That would cross the
  default OpenAI provider boundary, so the smoke intentionally stops at the
  validation path. The evaluation-run endpoints were stopped at the missing
  `DATABASE_URL` configuration path.
- This task did not repeat the optional both-succeeded-only comparison. The
  official all-40 development comparison remains the headline in the existing
  final evidence; no comparison metric was changed here.
- The exact human motivation for every individual regex token is not recorded
  in commit messages. Where the tests and saved development analysis establish
  a motivation, it is identified below; otherwise the motivation is marked as
  inferred from the code/test diff.

## 1. Deterministic advice/instruction filter audit

### Isolation from gold/development data

The current `agent/core.py` was read and searched without editing it. It does
not import `answers.csv`, `development_gold.csv`, a gold loader, an evaluator,
or an artifact/report reader. `validate_output()` receives only the model
payload, the `AgentInput`, and the `known_fact_ids` returned by the tool. The
filter receives only `quote` and `reason`.

There are four non-executable documentation/comment matches worth disclosing
so that “no gold/development-specific strings” is not overstated literally:

- line 4: “answers, labels, or evaluation data” in the module docstring;
- line 364: “evaluation unit” in the filter docstring/example;
- line 448: “not fuzzy matching or gold-assisted correction” in a validation
  comment;
- line 472: “not scored text” in a reason-normalization comment.

There is no `development`, `development_gold`, `expected_label`,
`gold_claim_id`, `difficulty_tags`, or equivalent executable field access in
`agent/core.py`. The only runtime metadata added by the agent is the generic
dataset/fact/evaluator version and the selected prompt/agent version. The
negative comment at line 448 is not a gold lookup or correction rule.

An offline AST check found only standard-library imports plus the shared
contracts/tracing/prompts modules, no direct `open`/`compile`/`eval`/`exec`
calls, and no executable identifier containing `gold`, `development`,
`answer_file`, or `evaluation_data`.

### Current executable rules

The rule is implemented by `_is_nonfactual_sentence()` at lines 359–381,
with helper predicates at lines 48–88 and 314–356. The complete decision
sequence is:

1. **Imperative lead detection (`_IMPERATIVE_START`, line 48).** A trimmed
   quote beginning with optional `please` and a listed imperative/meta verb
   (`buy`, `check`, `compare`, `choose`, `ignore`, `label`, `return`, `report`,
   `select`, `verify`, and the other listed tokens) is an instruction lead.
2. **Advice lead detection (`_ADVICE_START`, line 52).** Leads such as `you
   should/must/can/could/...`, `I'd recommend`, `my recommendation is`, `in my
   opinion`, and `personally` are advice leads.
3. **Independent factual-clause preservation (`_has_independent_factual_clause`,
   lines 322–337).** Instruction/advice text is split at hard `:` or `;`
   boundaries. Meta-action clauses are skipped; an ordinary factual clause is
   retained as a claim unit. The first clause has its instruction/advice lead
   stripped before the factual test.
4. **Meta-action rule (`_META_ACTION`, line 75).** If an instruction-lead quote
   contains `ignore`, `label`, `classify`, `return`, `output`, `state`, `say`,
   `report`, `claim`, `assert`, `treat`, `follow`, `reveal`, or `obey`, the quote
   is rejected unless a separate factual clause passes rule 3.
5. **Pure instruction/advice rule.** Any instruction/advice lead with no
   independent factual clause is rejected.
6. **Advice-context rule (`_ADVICE_CONTEXT`, line 57).** A quote beginning
   `before`, `after`, `when`, `while`, `if`, `unless`, or `for`, or containing
   `should`, `must`, `ought to`, `need to`, or `have to`, is rejected when
   `_has_product_fact()` cannot establish an ordinary product fact.
7. **Opinion-only rule (`_OPINION_ONLY`, line 63).** A quote containing terms
   such as `best`, `ideal`, `great`, `excellent`, `perfect`, `useful`,
   `worthwhile`, `recommended`, `right choice`, or `good choice` is rejected
   when it has no objective predicate.
8. **Non-factual reason rule (`_NONFACTUAL_REASON`, line 85).** A quote is
   rejected when its reason says `advice`, `instruction`, `opinion`,
   `recommendation`, `guidance`, `not a ... factual/product/specific`, or
   `buying explanation`, and the quote has no ordinary factual predicate.
9. **Factual predicate and context guard (`_FACTUAL_PREDICATE`,
   `_OBJECTIVE_PREDICATE`, `_PRODUCT_CONTEXT`, lines 67–83 and 340–356).
   `_has_product_fact()` requires a factual verb; objective predicates then
   require product context or a named/numbered target. A product-context match
   is not accepted for a pronoun-only copula such as `you are ...`.
10. **Conservative keep behavior.** If none of the rejection conditions fires,
     the claim is retained. This is why an imperative-plus-fact sentence such
     as `Compare the prices: Mini costs INR 1,299.` remains an evaluation unit.

### Rule-by-rule Git provenance

The following mapping is from `git blame` on the current lines and the two
commits that changed the filter. It records the first commit containing each
current rule/helper and, where a later commit expanded it, the expansion
commit. The dates are the commit dates in `+05:30` shown by Git.

| Current rule/helper | First current implementation | Later change | Motivation evidenced by tests or saved reports |
|---|---|---|---|
| Imperative lead detection (`_IMPERATIVE_START`) | `dcb1a89e`, 2026-10-20 01:12:46 | `bc5109b1`, 2026-10-20 02:45:24 expanded the token list | dcb added the pure-advice/injection test; bc added `always`, `never`, and meta-instruction variants. The commits do not record a more specific per-token reason. |
| Advice lead detection (`_ADVICE_START`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc tests cover `you should`, recommendations, and opinion-style leads. |
| Advice-context detection (`_ADVICE_CONTEXT`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc tests cover `before`, `for`, `should`, and related guidance context. |
| Opinion-only detection (`_OPINION_ONLY`) | `dcb1a89e`, 2026-10-20 01:12:46 | — | dcb test covers `best choice`; the v2 policy report classifies advice/opinion over-extraction as the development error. |
| Factual predicate (`_FACTUAL_PREDICATE`) | `dcb1a89e`, 2026-10-20 01:12:46 | `bc5109b1`, 2026-10-20 02:45:24 expanded exact verb forms | dcb tests keep a factual sentence while filtering advice/opinion/injection; bc tests keep independent product facts. |
| Objective predicate (`_OBJECTIVE_PREDICATE`) | `dcb1a89e`, 2026-10-20 01:12:46 | `bc5109b1`, 2026-10-20 02:45:24 expanded exact verb forms | Used by the opinion-only guard; the cited tests exercise objective product assertions versus opinions. |
| Meta-action detection (`_META_ACTION`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc tests cover `ignore`, `label`, `say`, and `return` forms; the dcb test includes the injection canary sentence. |
| Product-context guard (`_PRODUCT_CONTEXT`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc tests cover preserving product facts and rejecting generic advice/meta-instructions. |
| Independent-clause preservation (`_strip_instruction_lead`, `_has_independent_factual_clause`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc test explicitly retains `Compare the prices: Mini costs INR 1,299.` and a purchase lead followed by a product fact. |
| Product-fact predicate (`_has_product_fact`) | `bc5109b1`, 2026-10-20 02:45:24 | — | bc tests document named/numbered/product-context requirements and the pronoun-only copula guard. |
| Non-factual reason detection (`_NONFACTUAL_REASON`) | `dcb1a89e`, 2026-10-20 01:12:46 | — | dcb test supplies reasons containing advice/opinion/instruction wording and expects those spans to be filtered. |
| Filter decision (`_is_nonfactual_sentence`) and validation hook | `dcb1a89e`, 2026-10-20 01:12:46 | `bc5109b1`, 2026-10-20 02:45:24 changed the decision to use the lead/context/clause helpers | dcb introduced filtering at validation; bc hardened it for meta-instructions and factual-clause retention. |

### Git history and motivation evidence

| Rule/helper group | Added or changed | Motivation evidence and confidence |
|---|---|---|
| `_IMPERATIVE_START`, `_OPINION_ONLY`, `_FACTUAL_PREDICATE`, `_OBJECTIVE_PREDICATE`, `_NONFACTUAL_REASON`, and the first `_is_nonfactual_sentence()` | `dcb1a89e` (`test: close offline reliability and isolation gaps`), 2026-10-20 01:12:46 +05:30 | **Confirmed by diff/tests:** offline tests named `test_pure_advice_opinion_and_injection_are_filtered_but_facts_remain` exercise pure advice, opinion, an injection sentence, and retention of a factual sentence. The commit message itself does not name a production failure; the development-error link below is corroborating evidence, not a claim about an unrecorded private rationale. |
| `_IMPERATIVE_START` token expansion, `_ADVICE_START`, `_ADVICE_CONTEXT`, `_META_ACTION`, `_PRODUCT_CONTEXT`, `_strip_instruction_lead`, `_has_independent_factual_clause`, `_has_product_fact`, and the expanded predicate sets | `bc5109b1` (`fix: harden assessment runtime and evidence`), 2026-10-20 02:45:24 +05:30 | **Confirmed by diff/tests:** added variant tests for `always`, `never`, `you should`, recommendations, meta-instructions, and imperative-plus-independent-fact sentences. The guard is explicitly designed to avoid deleting factual clauses. Exact per-token rationale is **not recorded** beyond this test/diff evidence. |
| v2 prompt’s product/purchase wording policy (not executable `agent/core.py` logic) | Prompt/policy history includes `8293d18`, 2026-10-19 22:48:00 +05:30, and later `bc5109b1` | **Confirmed development-error motivation:** `tests/v2_policy/report.md` records 261 unmatched old-v1 predictions: 254 advice/instruction, 3 meta-instruction, 2 unsupported/advice-like, and 2 purchase-paperwork instructions. It says no provider experiment was launched. This explains the policy direction but is not evidence that the core regex was tuned with gold at inference time. |

### Motivation categories

- **Development errors:** confirmed as the problem being addressed by the
  saved offline baseline (`tests/v2_policy/report.md` and
  `evidence/analysis/v1-development-final-turn-gated-analysis.json`), not as a
  runtime input. The old analysis reports 261 unmatched predictions, including
  254 advice/instruction and 3 meta-instruction spans.
- **Injection canary:** confirmed as a test target. The dcb core test uses the
  exact meta-instruction form, and the preserved historical canaries for A07,
  A18, and A32 show the marker was previously extracted and both old v1/v2
  canary files had `passed: false` (`evidence/v1-final-injection-canary.json`
  and `evidence/v2-final-injection-canary.json`).
- **Check outputs:** **no direct evidence found** that any individual filter
  token was added because of a check-split output. The canary and policy tests
  are development/offline tests; check reports intentionally keep quality
  fields null.
- **Other:** factual-clause retention, protection against meta-instructions,
  and conservative avoidance of generic advice being mistaken for a product
  fact are directly stated by the helper docstrings/comments and tests.

## 2. Current four run reports and exchanges

The source of truth for the current runs is the four corrected-final artifact
directories below. `report.json` contains the full prediction and outcome
arrays; the inventory points to those files rather than rewriting or inventing
their rows.

| Run | Run ID | Outcome counts | Predictions | Development records | Quality metric |
|---|---|---:|---:|---|---:|
| v1 development | `1d30770a-933b-4982-83be-875d24c02682` | 22 ok / 18 errors / 40 scheduled | 201 | 175 missed, 0 unmatched extra, 9 wrong-label | joint F1 `0.6655112651646448` |
| v2 development | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | 26 ok / 14 errors / 40 scheduled | 231 | 145 missed, 0 unmatched extra, 14 wrong-label | joint F1 `0.71499176276771` |
| v1 check | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | 5 ok / 5 errors / 10 scheduled | 44 | missing/extra/wrong-label fields are null by check isolation | quality null |
| v2 check | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | 9 ok / 1 error / 10 scheduled | 83 | missing/extra/wrong-label fields are null by check isolation | quality null |

All four settings are present in each `config.json` and are also copied from
the saved settings into the inventory: provider `openai`, model
`gpt-5-mini-2025-08-07`, reasoning effort `minimal`, max completion tokens
`2000`, request timeout `15.0` seconds, and temperature `null`. The four
reports share source revision `a0020d06e0d71fbe83fd43236cdec3b33983d8b6`; v1/v2
prompt hashes differ as expected.

The complete source files and their digests are recorded in:

- `evidence/supplementary/current-four-run-inventory.json`
- `artifacts/v1-development-corrected-final/`
- `artifacts/v2-development-corrected-final/`
- `artifacts/v1-check-corrected-final/`
- `artifacts/v2-check-corrected-final/`

The owned exchange files are:

- `evidence/exchanges/v1-development.jsonl`
- `evidence/exchanges/v2-development.jsonl`
- `evidence/exchanges/v1-check.jsonl`
- `evidence/exchanges/v2-check.jsonl`

They are derived line-by-line from the corresponding saved artifact
`exchanges.jsonl`. The derivation removes raw payload-shaped keys and
`file_hashes` while retaining event kind, answer/trace IDs, tool names and
fact-ID summaries, model/provider settings, and error/attempt fields. Score
events are intentionally excluded from the exchange-only files; report metrics
remain available from the source reports and inventory. A search of the owned
files found no `input_data`, `output_data`, `messages`, `secret_key`,
`file_hashes`, or OpenAI key-shaped values. No source artifact was overwritten.

## 3. No-model FastAPI smoke

The actual command used `.venv/bin/python -m uvicorn claimlens.api.app:app` on
127.0.0.1:8765 with `OPENAI_API_KEY=`, `OPENAI_MODEL=`, `DATABASE_URL=`, and
Langfuse variables explicitly empty. The exact sanitized curl responses,
server process log, and statuses are in `evidence/run-smoke.txt`:

- `POST /classify` with `{}`: HTTP 422, `validation_error`; no valid classify
  request was sent, so the default agent and OpenAI provider were not reached.
- `POST /evaluation-runs` with the valid request shape: HTTP 500,
  `configuration_error`, because `DATABASE_URL` was empty.
- `GET /evaluation-runs/00000000-0000-0000-0000-000000000000`: HTTP 500,
  `configuration_error`, because `DATABASE_URL` was empty.
- Uvicorn started successfully and logged all three requests; the process was
  stopped cleanly. No provider/model/Langfuse request was initiated.

This is a **configuration/validation blocker receipt**, not evidence that a
successful live classify or database-backed evaluation run works. That stronger
path was intentionally not invoked under the no-credentials/no-model
constraint.

## 4. Verification commands for this audit

The owned evidence generator is offline-only and reads saved artifacts:

```text
.venv/bin/python evidence/supplementary/generate_audit_artifacts.py
```

The repository’s existing offline verification receipts remain unchanged:

```text
make test
make verify
```

The post-audit test run completed as:

```text
make test -> 102 passed, 1 warning (elapsed time is environment-dependent)
```

`make verify` was also rerun after the evidence-only changes and exited 0; both
corrected development reports remained `status: "verified"` with the metrics
shown in the saved receipt `evidence/verify-output.txt`.

No `git commit` was made by T3. The T3-owned outputs are limited to
`evidence/exchanges/**`, `evidence/supplementary/**`, and this note. The
existing `evidence/run-smoke.txt` was read and independently checked against a
fresh Uvicorn/curl run; it was not edited because that path is outside the
exclusive T3 ownership list.
