# Langfuse reviewer evidence

This is a sanitized, read-only export. Raw prompts, full answers, model outputs, tool payloads, score payloads, credentials, and environment values are not included.

## Collection

- Generated at: `2026-10-20T03:43:56.474938Z`
- Endpoint: `GET /api/public/v2/observations` with per-answer `traceId`, the completed run window ±1 minute, `limit=10`, and `fields=core,basic,io`.
- Serial throttle: `2.25` seconds minimum between requests; max `3` attempts for HTTP 429 with bounded backoff.
- Provider/model calls started by this audit: `0`.

## Current-run remote confirmation counts

| Run | Run ID | Baseline confirmed | Recovered from prior 429 | Current confirmed | Remaining inaccessible |
|---|---|---:|---:|---:|---:|
| `v1_development` | `1d30770a-933b-4982-83be-875d24c02682` | 32 | 8 | 40 | 0 |
| `v2_development` | `4ade6dff-2f02-40b3-8b25-72985c0ca141` | 34 | 6 | 40 | 0 |
| `v1_check` | `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a` | 10 | 0 | 10 | 0 |
| `v2_check` | `2f8469ef-e735-4eee-bb01-9b9a8dff7865` | 10 | 0 | 10 | 0 |

## Exact prior-429 re-query results

Each row below is one of the 14 traces that returned HTTP 429 in the previous audit. `recovered` means a later serial attempt returned non-empty observations; a trace still listed as `rate_limited` is not evidence of missing ingestion.

| Run | Answer | Trace ID | Final status | Attempts | Recovery | Final HTTP | Final observations |
|---|---|---|---|---:|---|---:|---:|
| `v1_development` | `A07` | `c549e7db787c4e13ac39ca9545027305` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A12` | `ca139fdf0f614fff8eeac7aaca7944a5` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A16` | `cfb5664e448146e9aac8b252963ac385` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A35` | `d23618a261924f1fb57c2d14aedcf5e7` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A09` | `ddce780300a547e8aba6fede42869c5e` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A22` | `de9cc44308af4cd08858f0ea19127a15` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A31` | `e0c22dd4f193437f9c2f5243c8c51fec` | `verified` | 1 | `True` | 200 | 3 |
| `v1_development` | `A32` | `e10cf596f3da45deba3c7c3c543a1fa7` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A24` | `af591f25021a43e8b4ddc8e3d889f331` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A30` | `b98eb3fdc71f4e78b7fd59fc9f9453ef` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A25` | `c595acc03d324639a41e83561f70e1ab` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A26` | `ce3d881597b94437834bbbfae9afb73f` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A39` | `d8a0873eb193488cb5bfba3fbb2d55f3` | `verified` | 1 | `True` | 200 | 3 |
| `v2_development` | `A28` | `e2b89bea04d44d62add23ebbf4ac4e32` | `verified` | 1 | `True` | 200 | 3 |

### Remaining inaccessible traces

- None after the bounded re-query set.

## v1_development

- Artifact: `artifacts/v1-development-corrected-final`
- Run ID: `1d30770a-933b-4982-83be-875d24c02682`; answer: `A19`; trace ID: `128b8c40fe8945cd9d78944d712a49e7`
- Remote status: `verified`; observations: `3`
- Answer text (≤200 chars): "For comparison shopping, start by naming the exact speaker and the particular alternative you are considering. Compare like conditions rather than collecting attractive numbers from different situatio"
- Local development scores: `extraction_f1=1.0`, `joint_f1=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`

| Observation/span ID | Name | Type | Level | Start | End | Duration ms |
|---|---|---|---|---|---|---:|
| `d4989e24780441a3` | `get_brand_facts` | `TOOL` | `DEFAULT` | `2026-10-19T21:34:11.254Z` | `2026-10-19T21:34:11.254Z` | `0.0` |
| `702e832f987d419d` | `model-turn-1` | `GENERATION` | `DEFAULT` | `2026-10-19T21:34:11.253Z` | `2026-10-19T21:34:11.253Z` | `0.0` |
| `0ceff680fd454622` | `claimlens.answer` | `SPAN` | `DEFAULT` | `2026-10-19T21:34:02.143Z` | `2026-10-19T21:34:02.143Z` | `0.0` |

## v2_development

- Artifact: `artifacts/v2-development-corrected-final`
- Run ID: `4ade6dff-2f02-40b3-8b25-72985c0ca141`; answer: `A01`; trace ID: `0c5e1d78d67a493386e8b19f80dc89a7`
- Remote status: `verified`; observations: `4`
- Answer text (≤200 chars): "Start with the exact model name before deciding what belongs in a weekend bag. Compare the purchase terms separately from the technical specifications, and keep any assumptions about your own usage vi"
- Local development scores: `extraction_f1=1.0`, `joint_f1=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `aggregate_extraction_f1=0.7611202635914333`, `aggregate_joint_f1=0.71499176276771`

| Observation/span ID | Name | Type | Level | Start | End | Duration ms |
|---|---|---|---|---|---|---:|
| `0d0af05add104769` | `model-turn-2` | `GENERATION` | `DEFAULT` | `2026-10-19T21:52:32.603Z` | `2026-10-19T21:52:32.603Z` | `0.0` |
| `705b1b65deeb4463` | `get_brand_facts` | `TOOL` | `DEFAULT` | `2026-10-19T21:52:03.197Z` | `2026-10-19T21:52:03.197Z` | `0.0` |
| `94afe556d2a842bb` | `model-turn-1` | `GENERATION` | `DEFAULT` | `2026-10-19T21:52:03.196Z` | `2026-10-19T21:52:03.196Z` | `0.0` |
| `67ad57a470514852` | `claimlens.answer` | `SPAN` | `DEFAULT` | `2026-10-19T21:52:01.548Z` | `2026-10-19T21:52:32.604Z` | `31056.0` |

## v1_check

- Artifact: `artifacts/v1-check-corrected-final`
- Run ID: `cbcc20f2-b4c8-4c2d-8718-76bd3a69002a`; answer: `A44`; trace ID: `166ac97df9d041f3921f71beb512811b`
- Remote status: `verified`; observations: `3`
- Answer text (≤200 chars): "For an outdoor gathering, think first about the conditions you expect rather than choosing a speaker from a single headline figure. Separate a performance statement from a warranty statement when read"
- Check-score policy: no gold-derived scores are included.

| Observation/span ID | Name | Type | Level | Start | End | Duration ms |
|---|---|---|---|---|---|---:|
| `9f88fc5a442047a0` | `get_brand_facts` | `TOOL` | `DEFAULT` | `2026-10-19T22:09:24.908Z` | `2026-10-19T22:09:24.908Z` | `0.0` |
| `38dac39c15544888` | `model-turn-1` | `GENERATION` | `DEFAULT` | `2026-10-19T22:09:24.907Z` | `2026-10-19T22:09:24.907Z` | `0.0` |
| `f8bd1e2fa75d4919` | `claimlens.answer` | `SPAN` | `DEFAULT` | `2026-10-19T22:09:21.973Z` | `2026-10-19T22:09:21.973Z` | `0.0` |

## v2_check

- Artifact: `artifacts/v2-check-corrected-final`
- Run ID: `2f8469ef-e735-4eee-bb01-9b9a8dff7865`; answer: `A47`; trace ID: `0fc42d24bbc94c7cb3f443ec163469b7`
- Remote status: `verified`; observations: `3`
- Answer text (≤200 chars): "Start with the exact model name before deciding what belongs in a weekend bag. Compare the purchase terms separately from the technical specifications, and keep any assumptions about your own usage vi"
- Check-score policy: no gold-derived scores are included.

| Observation/span ID | Name | Type | Level | Start | End | Duration ms |
|---|---|---|---|---|---|---:|
| `9da9ea52910f453c` | `model-turn-1` | `GENERATION` | `DEFAULT` | `2026-10-19T22:14:22.995Z` | `2026-10-19T22:14:22.995Z` | `0.0` |
| `8c90db4ece6946fe` | `get_brand_facts` | `TOOL` | `DEFAULT` | `2026-10-19T22:14:22.995Z` | `2026-10-19T22:14:22.995Z` | `0.0` |
| `50ee42124edf4248` | `claimlens.answer` | `SPAN` | `DEFAULT` | `2026-10-19T22:14:19.456Z` | `2026-10-19T22:14:19.456Z` | `0.0` |
