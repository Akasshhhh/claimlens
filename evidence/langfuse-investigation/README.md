# Langfuse investigation

## Result

The requested experiment run is `8cb56eec-6c54-4eab-aba2-317bee75d451` in
`artifacts/v1-development-final-turn-gated/`. Its local evidence has 40
`trace_start` rows and 40 local trace IDs, but no persisted
`trace_verification.json` receipt.

One existing trace was checked remotely, without starting a provider run:

- trace ID: `dc8e8ee41ef74834a52ed449edc783d6`
- authenticated `GET /api/public/v2/observations` with the run window returned
  HTTP 200 and four observations: `model-turn-2`, `get_brand_facts`,
  `model-turn-1`, and `claimlens.answer`
- the same trace ID in a current five-minute window returned HTTP 200 with
  `{"data":[],"meta":{}}`
- using the experiment run UUID as `traceId` also returned HTTP 200 with an
  empty data list

This confirms a query/identifier mismatch. It does not establish remote
existence for the other 39 traces.

The exact sanitized response bodies, hashes, environment-name presence checks,
errors, commands, and findings are in `report.json`.

## Non-blocking verifier recommendation

Keep verification outside provider execution and outside the completed-run
status transition:

1. Persist outcomes and the run metadata first.
2. Start a separate post-run verifier using the per-answer trace IDs, not the
   experiment run UUID.
3. Query a fixed window covering that run (`started_at - 1 minute` through
   `completed_at + 1 minute`) and use `limit=10&fields=core,basic`.
4. Poll for up to 1,200 seconds. Record each last status/body/error in a
   sanitized receipt.
5. Mark a trace `verified` only when the response has a non-empty `data` list.
   Record `timeout` for an empty response at the deadline, and leave the
   already completed provider experiment completed.

The current runner's strict verification path can raise after provider work is
finished. Integrating this recommendation requires a change at that runner
call site, which was outside this worker's permitted files; no source change
was made here.

## Checks

`python3 -m compileall -q claimlens/tracing.py` passed with no output.

The focused pytest command was attempted but the available interpreter does
not have pytest installed:

```text
/usr/local/bin/python3: No module named pytest
```
