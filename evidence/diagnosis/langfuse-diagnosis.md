# Langfuse diagnosis

## Historical v1 trace (explicitly historical)

- Artifact: `artifacts/v1-development-final-turn-gated`; run ID: `8cb56eec-6c54-4eab-aba2-317bee75d451`; answer: `A01`.
- Trace ID: `dc8e8ee41ef74834a52ed449edc783d6`; remote status: `verified`.
- Observations: `model-turn-2`, `get_brand_facts`, `model-turn-1`, `claimlens.answer`.
- Answer text (≤200 chars): "Start with the exact model name before deciding what belongs in a weekend bag. Compare the purchase terms separately from the technical specifications, and keep any assumptions about your own usage vi"
- What it showed: a real `get_brand_facts` tool call and two model turns completed. The saved local A01 answer receipt recorded 16 claim IDs, including 7 advice/instruction/opinion-only spans marked unknown. This is the actual historical trace used for diagnosis; it is not a corrected final run.

## Why v2 changed

The historical v1 behavior motivated a model-facing policy change: filter pure advice, opinions, imperative instructions, and injected instructions, while retaining sentences that also contain an independent factual clause. The change targets the advice/instruction extraction failure mode and does not alter the post-run verification boundary.

## Corrected v2 result

- Artifact: `artifacts/v2-development-corrected-final`; run ID: `4ade6dff-2f02-40b3-8b25-72985c0ca141`; answer: `A01`.
- Trace ID: `0c5e1d78d67a493386e8b19f80dc89a7`; remote status: `verified`; observations: `model-turn-2`, `get_brand_facts`, `model-turn-1`, `claimlens.answer`.
- Answer text (≤200 chars): "Start with the exact model name before deciding what belongs in a weekend bag. Compare the purchase terms separately from the technical specifications, and keep any assumptions about your own usage vi"
- Local development scores for this trace: `extraction_f1=1.0`, `joint_f1=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `label_correct=1.0`, `aggregate_extraction_f1=0.7611202635914333`, `aggregate_joint_f1=0.71499176276771`
- Corrected v2 development run metrics: `{"answer_errors": 14, "counts": {"answers": 40, "errors": 14, "ok": 26, "predictions": 231, "scheduled": 40}, "coverage": 0.5771276595744681, "extraction_f1": 0.7611202635914333, "joint_f1": 0.71499176276771, "macro_f1": 0.7144441342515044, "predictions": 231}`.
- Interpretation: the corrected v2 artifact has remote observation evidence and local development scores; the historical trace remains labeled historical and is not treated as a corrected result.

## Limits

- Check traces are observation-only exports with no gold-derived scores.
- The observed development gain is descriptive; the saved bootstrap interval includes zero.
