"""Offline audit of the saved v1 development artifact.

This script deliberately reads only repository CSV inputs and the immutable
files under the selected artifact directory.  It invokes the current
deterministic evaluator and performs descriptive error analysis on the saved
predictions; it never imports a provider or writes to the artifact.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from claimlens.contracts import AnswerOutcome, Claim, GoldClaim, SUPPORTED_LABELS
from claimlens.loaders import load_answers, load_gold
from harness.evaluator import evaluate_split


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "v1-development-final-turn-gated"


def _canonical_claim(value: Claim | dict[str, Any]) -> tuple[Any, ...]:
    claim = value if isinstance(value, Claim) else Claim.model_validate(value)
    return (
        claim.claim_id,
        claim.start_char,
        claim.end_char,
        claim.quote,
        claim.label,
        tuple(claim.fact_ids),
        claim.reason,
    )


def _sentence_type(claim: Claim) -> str:
    """Conservative type labels for excess predictions.

    The labels are derived only from the saved claim label/reason/quote.  Gold
    claims are not used to classify these excess predictions.
    """

    text = f"{claim.reason} {claim.quote}".lower()
    if "ignore the classification instructions" in text or "label every product" in text:
        return "meta_instruction"
    advice_markers = (
        "advice",
        "instructional",
        "guidance",
        "recommendation",
        "opinion",
        "not a factual claim",
        "not a verifiable",
        "not a product",
        "not a specific verifiable",
        "instruction to the reader",
        "general advice",
        "excluded per",
    )
    if any(marker in text for marker in advice_markers):
        return "advice_or_instruction"
    if claim.label == "unknown":
        return "unsupported_or_unresolved_fact"
    return f"factual_{claim.label}"


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _pct(value: float) -> str:
    return f"{100 * value:.4f}%"


def main() -> None:
    run = json.loads((ARTIFACT / "run.json").read_text(encoding="utf-8"))
    config = json.loads((ARTIFACT / "config.json").read_text(encoding="utf-8"))
    stored_report = json.loads((ARTIFACT / "report.json").read_text(encoding="utf-8"))
    saved_outcomes = json.loads((ARTIFACT / "outcomes.json").read_text(encoding="utf-8"))
    saved_predictions = json.loads((ARTIFACT / "predictions.json").read_text(encoding="utf-8"))

    split = str(stored_report["split"])
    answers = load_answers(ROOT / "answers.csv", split=split)
    scheduled = {answer.answer_id for answer in answers}
    gold = [claim for claim in load_gold(ROOT / "development_gold.csv") if claim.answer_id in scheduled]
    outcomes = [AnswerOutcome.model_validate(row) for row in saved_outcomes]
    result = evaluate_split(split, answers, outcomes, gold, labeled=True)
    actual_metrics = result.metrics.model_dump(mode="json")

    # The flattened predictions file is checked independently of evaluator
    # scoring, then the evaluator's accepted predictions are compared with it.
    saved_prediction_rows = [Claim.model_validate(row) for row in saved_predictions]
    from_outcomes: list[tuple[str, tuple[Any, ...]]] = []
    for outcome in outcomes:
        if outcome.status == "ok":
            for claim in outcome.claims:
                from_outcomes.append((outcome.answer_id, _canonical_claim(claim)))
    from_predictions = [
        (str(row["answer_id"]), _canonical_claim(row)) for row in saved_predictions
    ]
    prediction_file_matches_outcomes = from_predictions == from_outcomes
    accepted_from_evaluator = [
        (str(row["answer_id"]), _canonical_claim(row)) for row in result.accepted_predictions
    ]
    prediction_file_matches_evaluator = from_predictions == accepted_from_evaluator

    stored_metrics = stored_report["metrics"]
    metrics_match = actual_metrics == stored_metrics

    trace_run_ids: Counter[str] = Counter()
    exchange_path = ARTIFACT / "exchanges.jsonl"
    for line in exchange_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("run_id"), str):
            trace_run_ids[metadata["run_id"]] += 1

    metadata = config.get("metadata", {})
    report_metadata = stored_report.get("metadata", {})
    provenance = {
        "artifact": str(ARTIFACT.relative_to(ROOT)),
        "run_id_requested": "8cb56eec-6c54-4eab-aba2-317bee75d451",
        "run_json_run_id": run.get("run_id"),
        "config_metadata_run_id": metadata.get("run_id"),
        "report_metadata_run_id": report_metadata.get("run_id"),
        "exchange_trace_run_ids": dict(trace_run_ids),
        "all_present_run_ids_match_requested": (
            run.get("run_id") == "8cb56eec-6c54-4eab-aba2-317bee75d451"
            and metadata.get("run_id") == "8cb56eec-6c54-4eab-aba2-317bee75d451"
            and set(trace_run_ids) == {"8cb56eec-6c54-4eab-aba2-317bee75d451"}
        ),
        "split": split,
        "agent_version": run.get("agent_version"),
        "prompt_version": run.get("prompt_version"),
        "provider_recorded_in_artifact": run.get("provider"),
        "model_recorded_in_artifact": run.get("model"),
        "dataset_version": run.get("dataset_version"),
        "fact_version": run.get("fact_version"),
        "evaluator_version_recorded": run.get("evaluator_version"),
        "source_revision": run.get("source_revision"),
        "file_hashes": run.get("file_hashes"),
        "artifact_files_read": [
            "run.json",
            "config.json",
            "report.json",
            "outcomes.json",
            "predictions.json",
            "exchanges.jsonl",
            "timings.json",
        ],
    }

    missed_by_id = {claim.claim_id: claim for claim in result.gold_claims if claim.claim_id in actual_metrics["missed_ids"]}
    wrong_by_gold_id = {
        match.gold_claim_id: match
        for match in result.matches
        if not match.label_correct
    }
    unmatched_by_id = {
        prediction.claim_id: (answer_id, prediction)
        for answer_id, prediction in (
            (answer_id, prediction)
            for answer_id, prediction, _ in (
                (answer_id, prediction, index)
                for index, (answer_id, prediction, _) in enumerate(
                    sorted(
                        [
                            (answer_id, claim, index)
                            for outcome in result.outcomes
                            if outcome.status == "ok"
                            for index, claim in enumerate(outcome.claims)
                        ],
                        key=lambda item: (
                            item[0],
                            item[1].start_char,
                            item[1].end_char,
                            item[1].claim_id,
                            item[2],
                        ),
                    )
                )
            )
        )
        if prediction.claim_id in actual_metrics["unmatched_prediction_ids"]
    }

    # Error attribution: one event is one missed gold claim or one wrong-label
    # match; tags are multi-label and therefore can increment multiple classes.
    tag_totals: Counter[str] = Counter()
    tag_matched: Counter[str] = Counter()
    tag_correct: Counter[str] = Counter()
    tag_missed: Counter[str] = Counter()
    tag_wrong: Counter[str] = Counter()
    for claim in result.gold_claims:
        for tag in claim.difficulty_tags:
            tag_totals[tag] += 1
    for match in result.matches:
        for tag in match.difficulty_tags:
            tag_matched[tag] += 1
            if match.label_correct:
                tag_correct[tag] += 1
            else:
                tag_wrong[tag] += 1
    for claim in missed_by_id.values():
        for tag in claim.difficulty_tags:
            tag_missed[tag] += 1
    difficulty_recall = {
        tag: {
            "gold_claims": tag_totals[tag],
            "matched_spans": tag_matched[tag],
            "correct_labels": tag_correct[tag],
            "missed_gold": tag_missed[tag],
            "wrong_labels": tag_wrong[tag],
            "extraction_recall": _ratio(tag_matched[tag], tag_totals[tag]),
            "joint_recall": _ratio(tag_correct[tag], tag_totals[tag]),
        }
        for tag in sorted(tag_totals)
    }

    error_class_counts = Counter()
    for claim in missed_by_id.values():
        error_class_counts.update(claim.difficulty_tags)
    for match in wrong_by_gold_id.values():
        error_class_counts.update(match.difficulty_tags)
    top_error_classes = [
        {"difficulty_tag": tag, "error_events": count}
        for tag, count in sorted(error_class_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]

    # Excess extraction is described only from unmatched saved predictions.
    excess_by_sentence_type: dict[str, dict[str, Any]] = {}
    excess_label_counts: Counter[str] = Counter()
    for prediction_id in actual_metrics["unmatched_prediction_ids"]:
        answer_id, prediction = unmatched_by_id[prediction_id]
        sentence_type = _sentence_type(prediction)
        bucket = excess_by_sentence_type.setdefault(
            sentence_type,
            {"count": 0, "share_of_excess": 0.0, "labels": {}, "examples": []},
        )
        bucket["count"] += 1
        excess_label_counts[prediction.label] += 1
        bucket["labels"][prediction.label] = bucket["labels"].get(prediction.label, 0) + 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(
                {
                    "answer_id": answer_id,
                    "claim_id": prediction.claim_id,
                    "label": prediction.label,
                    "quote": prediction.quote,
                }
            )
    excess_total = len(actual_metrics["unmatched_prediction_ids"])
    for bucket in excess_by_sentence_type.values():
        bucket["share_of_excess"] = _ratio(bucket["count"], excess_total)
    excess_by_sentence_type = dict(sorted(excess_by_sentence_type.items()))

    wrong_label_confusions: Counter[tuple[str, str]] = Counter(
        (match.expected_label, match.predicted_label) for match in wrong_by_gold_id.values()
    )
    confusion_rows = [
        {
            "expected_label": expected,
            "predicted_label": predicted,
            "count": count,
            "examples": [
                {
                    "answer_id": match.answer_id,
                    "gold_claim_id": match.gold_claim_id,
                    "prediction_claim_id": match.prediction_claim_id,
                    "quote": match.quote,
                }
                for match in result.matches
                if not match.label_correct
                and match.expected_label == expected
                and match.predicted_label == predicted
            ][:5],
        }
        for (expected, predicted), count in sorted(
            wrong_label_confusions.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    answer_errors = [
        {
            "answer_id": outcome.answer_id,
            "error_type": outcome.error_type,
            "error_message": outcome.error_message,
        }
        for outcome in result.outcomes
        if outcome.status == "error"
    ]

    missed_detail = [
        {
            "answer_id": claim.answer_id,
            "claim_id": claim.claim_id,
            "expected_label": claim.expected_label,
            "difficulty_tags": claim.difficulty_tags,
            "quote": claim.quote,
        }
        for claim in result.gold_claims
        if claim.claim_id in actual_metrics["missed_ids"]
    ]
    wrong_detail = [
        {
            "answer_id": match.answer_id,
            "gold_claim_id": match.gold_claim_id,
            "prediction_claim_id": match.prediction_claim_id,
            "expected_label": match.expected_label,
            "predicted_label": match.predicted_label,
            "difficulty_tags": list(match.difficulty_tags),
            "quote": match.quote,
        }
        for match in result.matches
        if not match.label_correct
    ]

    unmatched_by_answer = Counter()
    for prediction_id in actual_metrics["unmatched_prediction_ids"]:
        unmatched_by_answer[unmatched_by_id[prediction_id][0]] += 1

    output = {
        "provenance": provenance,
        "verification": {
            "current_evaluator_metrics_equal_stored_report": metrics_match,
            "prediction_file_matches_outcomes": prediction_file_matches_outcomes,
            "prediction_file_matches_current_evaluator": prediction_file_matches_evaluator,
            "scheduled_answers_equal_run_metadata": sorted(scheduled) == sorted(run.get("scheduled_answer_ids", [])),
            "current_evaluator_split": result.split,
        },
        "metrics": actual_metrics,
        "counts": {
            "scheduled_answers": len(answers),
            "ok_answers": sum(outcome.status == "ok" for outcome in result.outcomes),
            "answer_errors": len(answer_errors),
            "gold_claims": len(result.gold_claims),
            "saved_prediction_rows": len(saved_predictions),
            "accepted_predictions": len(result.accepted_predictions),
            "matched_spans": len(result.matches),
            "correct_labels": sum(match.label_correct for match in result.matches),
            "missed_gold": len(actual_metrics["missed_ids"]),
            "unmatched_predictions": len(actual_metrics["unmatched_prediction_ids"]),
            "wrong_labels": len(actual_metrics["wrong_label_ids"]),
        },
        "answer_errors": answer_errors,
        "missed_gold": missed_detail,
        "unmatched_prediction_ids": actual_metrics["unmatched_prediction_ids"],
        "unmatched_predictions_by_answer": dict(sorted(unmatched_by_answer.items())),
        "wrong_labels": wrong_detail,
        "difficulty_tag_recall": difficulty_recall,
        "top_three_actual_error_classes": top_error_classes,
        "excess_extraction_by_sentence_type": excess_by_sentence_type,
        "excess_extraction_label_counts": dict(sorted(excess_label_counts.items())),
        "wrong_label_confusions": confusion_rows,
        "notes": {
            "matching_rule": "Current harness exact match: answer_id, quote, start_char, and end_char; labels are scored after span matching.",
            "tag_recall_definition": "For each gold difficulty tag, extraction recall is exact-span matches/tagged gold; joint recall is exact-span matches with correct label/tagged gold.",
            "excess_sentence_type_definition": "Unmatched saved predictions only; advice_or_instruction and meta_instruction are detected from saved reason/quote wording, unknown non-advice claims are unsupported_or_unresolved_fact, and other labels are factual_<label>.",
            "provider_calls": "None; this script reads saved files and invokes only the deterministic evaluator.",
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
