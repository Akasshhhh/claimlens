"""Offline audit for the requested frozen v1 development run.

The script reads the repository CSV inputs and the saved run files only.  It
calls the current deterministic evaluator, validates the flattened prediction
file against saved outcomes, and writes a new analysis file when ``--output``
is supplied.  It never writes below ``artifacts/`` and never imports a provider
client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from claimlens.contracts import AnswerOutcome, Claim
from claimlens.loaders import load_answers, load_gold
from harness.evaluator import evaluate_split


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "v1-development-final-turn-gated"
REQUESTED_RUN_ID = "8cb56eec-6c54-4eab-aba2-317bee75d451"
CLAIM_FIELDS = set(Claim.model_fields)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _canonical_claim(claim: Claim) -> str:
    return json.dumps(claim.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _claim_from_prediction_row(row: dict[str, Any]) -> Claim:
    # predictions.json is intentionally flattened with answer_id alongside
    # the Claim fields; answer_id is removed before validating the Claim.
    return Claim.model_validate({key: value for key, value in row.items() if key in CLAIM_FIELDS})


def _sentence_type(claim: Claim) -> str:
    """Classify excess predictions using only their saved text/reason."""

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


def _provenance(run: dict[str, Any], config: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    exchange_path = ARTIFACT / "exchanges.jsonl"
    exchange_run_ids: Counter[str] = Counter()
    exchange_rows = 0
    exchange_rows_with_run_id = 0
    for line in exchange_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        exchange_rows += 1
        row = json.loads(line)
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("run_id"), str):
            exchange_rows_with_run_id += 1
            exchange_run_ids[metadata["run_id"]] += 1

    stored_hashes = dict(run.get("file_hashes", {}))
    actual_hashes: dict[str, str] = {}
    missing_hash_inputs: list[str] = []
    for relative_path in sorted(stored_hashes):
        path = ROOT / relative_path
        if path.exists():
            actual_hashes[relative_path] = _sha256(path)
        else:
            missing_hash_inputs.append(relative_path)

    config_metadata = config.get("metadata", {})
    report_metadata = report.get("metadata", {})
    run_id_checks = {
        "run_json_run_id_matches_requested": run.get("run_id") == REQUESTED_RUN_ID,
        "config_metadata_run_id_matches_requested": (
            isinstance(config_metadata, dict)
            and config_metadata.get("run_id") == REQUESTED_RUN_ID
        ),
        "exchange_metadata_only_requested_run_id": (
            set(exchange_run_ids) == {REQUESTED_RUN_ID}
            and exchange_rows_with_run_id > 0
        ),
    }
    return {
        "artifact": str(ARTIFACT.relative_to(ROOT)),
        "requested_run_id": REQUESTED_RUN_ID,
        "run_json_run_id": run.get("run_id"),
        "config_metadata_run_id": config_metadata.get("run_id") if isinstance(config_metadata, dict) else None,
        "report_metadata_run_id": report_metadata.get("run_id") if isinstance(report_metadata, dict) else None,
        "exchange_run_id_counts": dict(sorted(exchange_run_ids.items())),
        "exchange_rows": exchange_rows,
        "exchange_rows_with_run_id": exchange_rows_with_run_id,
        "exchange_rows_without_run_id": exchange_rows - exchange_rows_with_run_id,
        "run_id_checks": run_id_checks,
        "all_run_id_checks_pass": all(run_id_checks.values()),
        "split": run.get("split"),
        "agent_version": run.get("agent_version"),
        "prompt_version": run.get("prompt_version"),
        "provider_recorded_in_artifact": run.get("provider"),
        "model_recorded_in_artifact": run.get("model"),
        "dataset_version": run.get("dataset_version"),
        "fact_version": run.get("fact_version"),
        "evaluator_version_recorded": run.get("evaluator_version"),
        "source_revision": run.get("source_revision"),
        "stored_file_hashes": stored_hashes,
        "actual_file_hashes": actual_hashes,
        "file_hashes_match": stored_hashes == actual_hashes and not missing_hash_inputs,
        "missing_hash_inputs": missing_hash_inputs,
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


def _difficulty_recall(result: Any) -> dict[str, dict[str, Any]]:
    totals: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    missed: Counter[str] = Counter()
    wrong: Counter[str] = Counter()
    missed_ids = set(result.metrics.missed_ids)
    for claim in result.gold_claims:
        for tag in claim.difficulty_tags:
            totals[tag] += 1
            if claim.claim_id in missed_ids:
                missed[tag] += 1
    for match in result.matches:
        for tag in match.difficulty_tags:
            matched[tag] += 1
            if match.label_correct:
                correct[tag] += 1
            else:
                wrong[tag] += 1
    return {
        tag: {
            "gold_claims": totals[tag],
            "matched_spans": matched[tag],
            "correct_labels": correct[tag],
            "missed_gold": missed[tag],
            "wrong_labels": wrong[tag],
            "extraction_recall": _ratio(matched[tag], totals[tag]),
            "joint_recall": _ratio(correct[tag], totals[tag]),
        }
        for tag in sorted(totals)
    }


def _build_analysis() -> dict[str, Any]:
    artifact_paths = [
        ARTIFACT / "run.json",
        ARTIFACT / "config.json",
        ARTIFACT / "report.json",
        ARTIFACT / "outcomes.json",
        ARTIFACT / "predictions.json",
        ARTIFACT / "exchanges.jsonl",
        ARTIFACT / "timings.json",
    ]
    before_artifact_bytes = {path.name: path.read_bytes() for path in artifact_paths}

    run = _read_json(ARTIFACT / "run.json")
    config = _read_json(ARTIFACT / "config.json")
    stored_report = _read_json(ARTIFACT / "report.json")
    saved_outcomes = _read_json(ARTIFACT / "outcomes.json")
    saved_predictions = _read_json(ARTIFACT / "predictions.json")

    split = str(stored_report["split"])
    answers = load_answers(ROOT / "answers.csv", split=split)
    scheduled_ids = tuple(sorted(answer.answer_id for answer in answers))
    gold = [claim for claim in load_gold(ROOT / "development_gold.csv") if claim.answer_id in scheduled_ids]
    outcomes = [AnswerOutcome.model_validate(row) for row in saved_outcomes]
    result = evaluate_split(split, answers, outcomes, gold, labeled=True)
    actual_metrics = result.metrics.model_dump(mode="json")
    stored_metrics = stored_report.get("metrics")

    # Compare flattened predictions while preserving the file/order checks.
    saved_prediction_pairs = [
        (str(row["answer_id"]), _canonical_claim(_claim_from_prediction_row(row)))
        for row in saved_predictions
    ]
    outcome_prediction_pairs = [
        (outcome.answer_id, _canonical_claim(claim))
        for outcome in outcomes
        if outcome.status == "ok"
        for claim in outcome.claims
    ]
    evaluator_prediction_pairs = [
        (str(row["answer_id"]), _canonical_claim(Claim.model_validate({key: value for key, value in row.items() if key in CLAIM_FIELDS})))
        for row in result.accepted_predictions
    ]

    accepted_by_claim_id: dict[str, tuple[str, Claim]] = {}
    for answer_id, prediction, _ in sorted(
        [
            (outcome.answer_id, claim, index)
            for outcome in result.outcomes
            if outcome.status == "ok"
            for index, claim in enumerate(outcome.claims)
        ],
        key=lambda item: (item[0], item[1].start_char, item[1].end_char, item[1].claim_id, item[2]),
    ):
        accepted_by_claim_id[prediction.claim_id] = (answer_id, prediction)

    missed_ids = set(actual_metrics["missed_ids"])
    unmatched_ids = set(actual_metrics["unmatched_prediction_ids"])
    missed_gold = [
        {
            "answer_id": claim.answer_id,
            "claim_id": claim.claim_id,
            "expected_label": claim.expected_label,
            "difficulty_tags": list(claim.difficulty_tags),
            "quote": claim.quote,
        }
        for claim in result.gold_claims
        if claim.claim_id in missed_ids
    ]
    unmatched_predictions: list[dict[str, Any]] = []
    unmatched_by_answer: Counter[str] = Counter()
    for claim_id in actual_metrics["unmatched_prediction_ids"]:
        answer_id, prediction = accepted_by_claim_id[claim_id]
        unmatched_by_answer[answer_id] += 1
        unmatched_predictions.append(
            {
                "answer_id": answer_id,
                "claim_id": prediction.claim_id,
                "label": prediction.label,
                "start_char": prediction.start_char,
                "end_char": prediction.end_char,
                "quote": prediction.quote,
            }
        )

    wrong_labels = [
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

    error_class_counts: Counter[str] = Counter()
    for claim in result.gold_claims:
        if claim.claim_id in missed_ids:
            error_class_counts.update(claim.difficulty_tags)
    for match in result.matches:
        if not match.label_correct:
            error_class_counts.update(match.difficulty_tags)
    top_three_error_classes = [
        {"difficulty_tag": tag, "error_events": count}
        for tag, count in sorted(error_class_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    ]

    excess_by_sentence_type: dict[str, dict[str, Any]] = {}
    excess_label_counts: Counter[str] = Counter()
    for row in unmatched_predictions:
        _answer_id, prediction = accepted_by_claim_id[row["claim_id"]]
        sentence_type = _sentence_type(prediction)
        bucket = excess_by_sentence_type.setdefault(
            sentence_type,
            {"count": 0, "share_of_excess": 0.0, "labels": {}, "examples": []},
        )
        bucket["count"] += 1
        bucket["labels"][prediction.label] = bucket["labels"].get(prediction.label, 0) + 1
        excess_label_counts[prediction.label] += 1
        if len(bucket["examples"]) < 5:
            bucket["examples"].append(
                {
                    "answer_id": row["answer_id"],
                    "claim_id": row["claim_id"],
                    "label": prediction.label,
                    "quote": prediction.quote,
                }
            )
    excess_total = len(unmatched_predictions)
    for bucket in excess_by_sentence_type.values():
        bucket["share_of_excess"] = _ratio(bucket["count"], excess_total)
    excess_by_sentence_type = dict(sorted(excess_by_sentence_type.items()))

    wrong_label_confusion_counts: Counter[tuple[str, str]] = Counter(
        (match.expected_label, match.predicted_label)
        for match in result.matches
        if not match.label_correct
    )
    wrong_label_confusions = []
    for (expected_label, predicted_label), count in sorted(
        wrong_label_confusion_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        wrong_label_confusions.append(
            {
                "expected_label": expected_label,
                "predicted_label": predicted_label,
                "count": count,
                "examples": [
                    {
                        "answer_id": match.answer_id,
                        "gold_claim_id": match.gold_claim_id,
                        "prediction_claim_id": match.prediction_claim_id,
                        "quote": match.quote,
                    }
                    for match in result.matches
                    if (
                        not match.label_correct
                        and match.expected_label == expected_label
                        and match.predicted_label == predicted_label
                    )
                ][:5],
            }
        )

    answer_errors = [
        {
            "answer_id": outcome.answer_id,
            "error_type": outcome.error_type,
            "error_message": outcome.error_message,
        }
        for outcome in result.outcomes
        if outcome.status == "error"
    ]

    after_artifact_bytes = {path.name: path.read_bytes() for path in artifact_paths}
    artifact_unchanged = before_artifact_bytes == after_artifact_bytes
    provenance = _provenance(run, config, stored_report)

    verification = {
        "current_evaluator_metrics_equal_stored_report": actual_metrics == stored_metrics,
        "prediction_file_matches_saved_outcomes_in_file_order": saved_prediction_pairs == outcome_prediction_pairs,
        "prediction_file_matches_current_evaluator_in_evaluator_order": saved_prediction_pairs == evaluator_prediction_pairs,
        "prediction_file_and_outcome_multisets_equal": Counter(saved_prediction_pairs) == Counter(outcome_prediction_pairs),
        "scheduled_answers_equal_run_metadata": list(scheduled_ids) == sorted(run.get("scheduled_answer_ids", [])),
        "current_evaluator_split": result.split,
        "artifact_files_unchanged_during_analysis": artifact_unchanged,
        "provider_or_api_calls_made": False,
    }

    stdout_summary = {
        "status": "completed",
        "offline": True,
        "run_id": REQUESTED_RUN_ID,
        "metrics_match": actual_metrics == stored_metrics,
        "provenance_run_id_checks_pass": provenance["all_run_id_checks_pass"],
        "input_hashes_match": provenance["file_hashes_match"],
        "artifact_unchanged": artifact_unchanged,
        "counts": {
            "gold_claims": len(result.gold_claims),
            "predictions": len(result.accepted_predictions),
            "matched_spans": len(result.matches),
            "correct_labels": sum(match.label_correct for match in result.matches),
            "missed_gold": len(missed_gold),
            "unmatched_predictions": len(unmatched_predictions),
            "wrong_labels": len(wrong_labels),
            "answer_errors": len(answer_errors),
        },
    }
    stdout_text = json.dumps(stdout_summary, indent=2, sort_keys=True)

    return {
        "schema_version": "claimlens-offline-analysis-v1",
        "analysis_scope": {
            "run_id": REQUESTED_RUN_ID,
            "artifact": str(ARTIFACT.relative_to(ROOT)),
            "source": "saved outcomes.json and predictions.json plus repository answers.csv/development_gold.csv",
            "evaluator": "current harness.evaluator.evaluate_split",
            "offline": True,
        },
        "blockers": [],
        "provenance": provenance,
        "verification": verification,
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
            "missed_gold": len(missed_gold),
            "unmatched_predictions": len(unmatched_predictions),
            "wrong_labels": len(wrong_labels),
        },
        "answer_errors": answer_errors,
        "missed_gold": missed_gold,
        "missed_gold_ids": actual_metrics["missed_ids"],
        "unmatched_predictions": unmatched_predictions,
        "unmatched_prediction_ids": actual_metrics["unmatched_prediction_ids"],
        "unmatched_predictions_by_answer": dict(sorted(unmatched_by_answer.items())),
        "wrong_labels": wrong_labels,
        "wrong_label_ids": actual_metrics["wrong_label_ids"],
        "difficulty_tag_recall": _difficulty_recall(result),
        "top_three_actual_error_classes": top_three_error_classes,
        "excess_extraction_by_sentence_type": excess_by_sentence_type,
        "excess_extraction_label_counts": dict(sorted(excess_label_counts.items())),
        "wrong_label_confusions": wrong_label_confusions,
        "notes": {
            "matching_rule": "Current evaluator exact match requires answer_id, quote, start_char, and end_char; labels are scored after span matching.",
            "coverage": "Current evaluator coverage is correct_labels / gold_claims.",
            "difficulty_tag_recall": "Tags are multi-label; extraction_recall is exact-span matches/tagged gold and joint_recall is correct-label matches/tagged gold.",
            "top_error_classes": "Difficulty tags attached to missed gold claims and wrong-label matches; one event can increment multiple tags.",
            "excess_sentence_types": "Unmatched saved predictions only. Categories use saved reason/quote wording and predicted label; no provider or new inference is used.",
            "wrong_label_confusions": "Exact-span matches whose saved predicted label differs from the saved gold expected_label.",
            "report_metadata_note": "report.json has no run_id in metadata if report_metadata_run_id is null; run.json, config.json, and every exchange metadata row are checked.",
        },
        "reproduction": {
            "command": "PYTHONPATH=. .venv/bin/python evidence/analysis/v1_development_final_turn_gated_analysis.py --output evidence/analysis/v1-development-final-turn-gated-analysis.json",
            "stdout_summary": stdout_summary,
            "stdout_exact": stdout_text,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new JSON output path; refuses to overwrite an existing file")
    args = parser.parse_args()
    analysis = _build_analysis()
    payload = json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        destination = args.output if args.output.is_absolute() else ROOT / args.output
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite existing analysis file: {destination}")
        if not destination.parent.exists():
            raise FileNotFoundError(f"analysis directory does not exist: {destination.parent}")
        destination.write_text(payload, encoding="utf-8", newline="\n")
    print(json.dumps(analysis["reproduction"]["stdout_summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
