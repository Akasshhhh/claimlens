"""Import saved JSON evaluation artifacts into PostgreSQL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from claimlens.contracts import AnswerOutcome, Claim

from .repository import PostgresRunRepository


def _load_artifact(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        payload = {"records": rows}
    if not isinstance(payload, Mapping):
        raise ValueError("run artifact must contain a JSON object")
    return dict(payload)


def _records(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    records = payload.get("records")
    if not isinstance(records, list):
        return dict(payload), [], None
    metadata: dict[str, Any] = {}
    outcomes: list[dict[str, Any]] = []
    metrics: Any = None
    for item in records:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind", item.get("type", item.get("record_type")))
        if kind in {"run", "metadata", "run_metadata"} or "run_id" in item and "answer_id" not in item:
            metadata.update(item)
        elif kind in {"metrics", "scores"}:
            metrics = item.get("metrics", item.get("scores"))
        elif isinstance(item.get("answer_outcome"), Mapping):
            outcomes.append(dict(item["answer_outcome"]))
        elif kind in {"answer", "answer_result", "outcome"} or "answer_id" in item:
            outcomes.append(dict(item))
    return metadata, outcomes, metrics


def _metric_counts(metrics: Any, outcomes: list[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scheduled": len(outcomes),
        "answers": len(outcomes),
        "ok": sum(item.get("status") == "ok" for item in outcomes),
        "errors": sum(item.get("status") == "error" for item in outcomes),
        "predictions": sum(len(item.get("claims") or []) for item in outcomes),
    }
    if isinstance(metrics, Mapping):
        for key in ("gold_claims", "predictions", "matched_spans", "correct_labels", "answer_errors"):
            if key in metrics:
                result[key] = metrics[key]
    return result


def import_artifact(
    path: str | Path | PostgresRunRepository,
    repository: PostgresRunRepository | str | Path | None = None,
    *,
    run_id: str | None = None,
) -> str:
    """Import a JSON report or JSONL run artifact and return its run UUID."""

    # Accept both natural call forms used by small scripts:
    # ``import_artifact(path, repository)`` and
    # ``import_artifact(repository, path)``.
    if not isinstance(path, (str, Path)) and isinstance(repository, (str, Path)):
        path, repository = repository, path
    if repository is None or not hasattr(repository, "create_run"):
        raise TypeError("a PostgreSQL run repository is required")

    payload = _load_artifact(path)
    record_metadata, record_outcomes, record_metrics = _records(payload)
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), Mapping) else {}
    if isinstance(payload.get("run"), Mapping):
        metadata.update(payload["run"])
    metadata.update(record_metadata)
    metrics = payload.get("metrics", payload.get("scores", record_metrics))
    if not isinstance(metrics, Mapping):
        metrics = None
    raw_outcomes = payload.get(
        "answer_outcomes",
        payload.get("outcomes", payload.get("answers", payload.get("answer_results"))),
    )
    outcomes: list[dict[str, Any]] = []
    if isinstance(raw_outcomes, list):
        outcomes.extend(item for item in raw_outcomes if isinstance(item, Mapping))
    outcomes.extend(record_outcomes)

    # JSON reports also carry accepted predictions separately.  Add them to an
    # outcome only when the outcome did not already include its claims.
    predictions_by_answer: dict[str, list[dict[str, Any]]] = {}
    raw_predictions = payload.get("predictions", payload.get("claim_results"))
    if isinstance(raw_predictions, list):
        for prediction in raw_predictions:
            if isinstance(prediction, Mapping) and isinstance(prediction.get("answer_id"), str):
                predictions_by_answer.setdefault(prediction["answer_id"], []).append(
                    {key: value for key, value in prediction.items() if key != "answer_id"}
                )

    matches_by_prediction: dict[str, dict[str, Any]] = {}
    raw_matches = payload.get("matches")
    if isinstance(raw_matches, list):
        for match in raw_matches:
            if isinstance(match, Mapping) and isinstance(match.get("prediction_claim_id"), str):
                matches_by_prediction[match["prediction_claim_id"]] = {
                    "gold_claim_id": match.get("gold_claim_id"),
                    "expected_label": match.get("expected_label"),
                    "label_correct": match.get("label_correct"),
                    "match": dict(match),
                }

    by_answer: dict[str, dict[str, Any]] = {}
    for item in outcomes:
        answer_id = item.get("answer_id")
        if isinstance(answer_id, str):
            row = dict(item)
            if not row.get("claims") and answer_id in predictions_by_answer:
                row["claims"] = predictions_by_answer[answer_id]
            row["claims"] = [
                {
                    **dict(claim),
                    **matches_by_prediction.get(str(claim.get("claim_id")), {}),
                }
                for claim in (row.get("claims") or [])
                if isinstance(claim, Mapping)
            ]
            by_answer[answer_id] = row
    if not by_answer and isinstance(payload.get("answer_scores"), list):
        for score in payload["answer_scores"]:
            if not isinstance(score, Mapping) or not isinstance(score.get("answer_id"), str):
                continue
            by_answer[score["answer_id"]] = {
                "answer_id": score["answer_id"],
                "status": score.get("status", "ok"),
                "latency_ms": score.get("latency_ms", 0),
                "error_type": score.get("error_type"),
                "error_message": score.get("error_message"),
                "claims": predictions_by_answer.get(score["answer_id"], []),
            }
    scheduled = payload.get("scheduled_answer_ids")
    if isinstance(scheduled, list):
        for answer_id in scheduled:
            if isinstance(answer_id, str) and answer_id not in by_answer:
                by_answer[answer_id] = {
                    "answer_id": answer_id,
                    "status": "error",
                    "latency_ms": 0,
                    "error_type": "artifact_missing_outcome",
                    "error_message": "saved artifact omitted terminal answer outcome",
                    "claims": [],
                }
    normalized = [by_answer[key] for key in sorted(by_answer)]
    run_meta = {
        **metadata,
        "run_id": run_id or payload.get("run_id") or metadata.get("run_id"),
        "split": payload.get("split", metadata.get("split", "unknown")),
        "agent_version": payload.get("agent_version", metadata.get("agent_version", "unknown")),
        "prompt_version": payload.get("prompt_version", metadata.get("prompt_version")),
        "dataset_version": payload.get("dataset_version", metadata.get("dataset_version")),
        "evaluator_version": payload.get("evaluator_version", metadata.get("evaluator_version")),
        "fact_version": payload.get("fact_version", metadata.get("fact_version")),
        "provider": payload.get("provider", metadata.get("provider")),
        "model": payload.get("model", metadata.get("model")),
        "settings": payload.get("settings", metadata.get("settings", {})),
        "config": payload.get("config", metadata.get("config", {})),
        "source_revision": payload.get("source_revision", metadata.get("source_revision")),
        "file_hashes": payload.get("file_hashes", metadata.get("file_hashes", {})),
        "status": payload.get("status", "completed"),
        "counts": payload.get("counts", _metric_counts(metrics, normalized)),
        "metrics": metrics,
        "available_metrics": [key for key, value in (metrics or {}).items() if value is not None],
        "metadata": metadata,
    }
    imported_id = repository.create_run(run_meta)
    for outcome in normalized:
        raw_claims = list(outcome.get("claims") or [])
        # Validate the public contract while retaining optional match metadata
        # in the JSONB-backed claim row.
        core_claims: list[dict[str, Any]] = []
        for claim in raw_claims:
            if not isinstance(claim, Mapping):
                raise ValueError("saved artifact contains a non-object claim")
            core = {key: claim[key] for key in ("claim_id", "start_char", "end_char", "quote", "label", "fact_ids", "reason") if key in claim}
            core_claims.append(Claim.model_validate(core).model_dump(mode="json"))
        core_outcome = {
            key: outcome[key]
            for key in ("answer_id", "status", "latency_ms", "trace_id", "error_type", "error_message")
            if key in outcome
        }
        if "trace_id" not in core_outcome and "trace_ref" in outcome:
            core_outcome["trace_id"] = outcome["trace_ref"]
        core_outcome.setdefault("latency_ms", 0)
        core_outcome["claims"] = core_claims
        validated = AnswerOutcome.model_validate(core_outcome)
        raw = validated.model_dump(mode="json")
        raw["claims"] = raw_claims
        repository.save_outcome(imported_id, raw)
    repository.finish_run(imported_id, str(run_meta["status"]), run_meta["counts"], metrics)
    return imported_id


import_run_artifact = import_artifact


__all__ = ["import_artifact", "import_run_artifact"]
