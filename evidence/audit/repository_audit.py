"""Offline, read-only checks for the submitted ClaimLens evidence.

This module deliberately audits files and metadata rather than starting a model,
database, or Langfuse run.  It is safe to run on a clean checkout with the
already-saved artifacts present.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_RUNS: dict[str, tuple[str, str, int]] = {
    "v1_development": ("v1-development-rerun", "v1", 40),
    "v2_development": ("v2-development", "v2", 40),
    "v1_check": ("v1-check", "v1", 10),
    "v2_check": ("v2-check", "v2", 10),
}

REQUIRED_ARTIFACT_FILES = (
    "run.json",
    "config.json",
    "outcomes.json",
    "predictions.json",
    "report.json",
    "timings.json",
    "exchanges.jsonl",
    "trace_verification.json",
    "trace_exports.json",
)

RUN_METADATA_KEYS = {
    "run_id",
    "split",
    "agent_version",
    "prompt_version",
    "dataset_version",
    "evaluator_version",
    "fact_version",
    "provider",
    "model",
    "settings",
    "source_revision",
    "file_hashes",
    "scheduled_answer_ids",
    "trace_ids",
}

CHECK_NULL_METRICS = (
    "gold_claims",
    "matched_spans",
    "correct_labels",
    "extraction_precision",
    "extraction_recall",
    "extraction_f1",
    "joint_precision",
    "joint_recall",
    "joint_f1",
    "coverage",
    "per_label",
    "macro_f1",
    "confusion_matrix",
    "missed_ids",
    "unmatched_prediction_ids",
    "wrong_label_ids",
)


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_audit(root: Path, name: str, version: str, scheduled: int) -> dict[str, Any]:
    directory = root / "artifacts" / name
    problems: list[str] = []
    missing = [filename for filename in REQUIRED_ARTIFACT_FILES if not (directory / filename).exists()]
    if missing:
        return {"status": "incomplete", "artifact": str(directory), "missing": missing, "problems": missing}

    try:
        run = _json(directory / "run.json")
        config = _json(directory / "config.json")
        outcomes = _json(directory / "outcomes.json")
        predictions = _json(directory / "predictions.json")
        report = _json(directory / "report.json")
        verification = _json(directory / "trace_verification.json")
        exports = _json(directory / "trace_exports.json")
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return {
            "status": "incomplete",
            "artifact": str(directory),
            "missing": [],
            "problems": [f"invalid JSON: {type(exc).__name__}: {exc}"],
        }

    if not isinstance(run, dict):
        problems.append("run.json is not an object")
        run = {}
    if not isinstance(config, dict) or not isinstance(config.get("metadata"), dict):
        problems.append("config.json has no metadata object")
    if not isinstance(outcomes, list) or not isinstance(predictions, list):
        problems.append("outcomes.json and predictions.json must be lists")
        outcomes = outcomes if isinstance(outcomes, list) else []
        predictions = predictions if isinstance(predictions, list) else []
    if not isinstance(report, dict):
        problems.append("report.json is not an object")
        report = {}
    if not isinstance(verification, list) or not isinstance(exports, list):
        problems.append("trace receipts must be lists")
        verification = verification if isinstance(verification, list) else []
        exports = exports if isinstance(exports, list) else []

    missing_metadata = sorted(RUN_METADATA_KEYS - set(run))
    if missing_metadata:
        problems.append(f"run metadata missing: {', '.join(missing_metadata)}")
    if run.get("agent_version") != version:
        problems.append("agent version does not match required run")

    scheduled_ids = run.get("scheduled_answer_ids")
    if not isinstance(scheduled_ids, list) or len(scheduled_ids) != scheduled:
        problems.append("scheduled answer count is not the required split size")
        scheduled_ids = scheduled_ids if isinstance(scheduled_ids, list) else []
    expected_ids = set(scheduled_ids)
    outcome_ids = [item.get("answer_id") for item in outcomes if isinstance(item, dict)]
    if len(outcome_ids) != scheduled or set(outcome_ids) != expected_ids or len(set(outcome_ids)) != len(outcome_ids):
        problems.append("outcomes do not contain one terminal row per scheduled answer")

    counts = run.get("counts") if isinstance(run.get("counts"), dict) else {}
    status_counts = Counter(item.get("status") for item in outcomes if isinstance(item, dict))
    if counts.get("answers") != len(outcomes):
        problems.append("run answer count differs from outcomes.json")
    if counts.get("ok") != status_counts.get("ok", 0) or counts.get("errors") != status_counts.get("error", 0):
        problems.append("run status counts differ from outcomes.json")
    if counts.get("predictions") != len(predictions):
        problems.append("run prediction count differs from predictions.json")

    report_outcomes = report.get("answer_outcomes")
    report_predictions = report.get("predictions")
    if not isinstance(report_outcomes, list) or len(report_outcomes) != len(outcomes):
        problems.append("report answer outcomes do not match outcomes.json")
    if not isinstance(report_predictions, list) or len(report_predictions) != len(predictions):
        problems.append("report predictions do not match predictions.json")

    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    if run.get("split") == "development":
        if report.get("labeled") is not True or metrics.get("gold_claims") != 376:
            problems.append("development report is not a labeled 376-claim report")
        if any(metrics.get(key) is None for key in ("extraction_f1", "joint_f1", "macro_f1")):
            problems.append("development quality metrics are missing")
    elif run.get("split") == "check":
        if report.get("labeled") is not False or report.get("gold_claims") not in (None, []):
            problems.append("check report exposes gold labels")
        if any(metrics.get(key) is not None for key in CHECK_NULL_METRICS):
            problems.append("check report exposes gold-derived quality metrics")

    trace_ids = run.get("trace_ids") if isinstance(run.get("trace_ids"), list) else []
    verification_ids = {item.get("trace_id") for item in verification if isinstance(item, dict)}
    if len(trace_ids) != scheduled or len(set(trace_ids)) != len(trace_ids):
        problems.append("trace_ids do not cover the scheduled answers uniquely")
    if len(verification) != len(trace_ids) or verification_ids != set(trace_ids):
        problems.append("trace_verification.json does not cover every trace")
    if any(not isinstance(item, dict) or not item.get("observation_count") for item in verification):
        problems.append("at least one trace receipt has no observations")
    if len(exports) != len(verification):
        problems.append("trace_exports.json does not mirror trace verification")

    event_counts: Counter[str] = Counter()
    event_errors: list[str] = []
    try:
        for line_number, line in enumerate((directory / "exchanges.jsonl").read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                event_errors.append(f"line {line_number} is not an object")
                continue
            event_counts[str(event.get("kind", ""))] += 1
    except (OSError, json.JSONDecodeError) as exc:
        event_errors.append(f"invalid exchanges.jsonl: {type(exc).__name__}: {exc}")
    if event_errors:
        problems.extend(event_errors)
    for kind in ("trace_start", "answer_end"):
        if event_counts[kind] != scheduled:
            problems.append(f"exchanges.jsonl has {event_counts[kind]} {kind} events, expected {scheduled}")
    if event_counts["generation"] == 0 or event_counts["tool_observation"] == 0:
        problems.append("exchanges.jsonl has no generation or tool observation evidence")

    return {
        "status": "complete" if not problems else "incomplete",
        "artifact": str(directory),
        "split": run.get("split"),
        "agent_version": run.get("agent_version"),
        "run_id": run.get("run_id"),
        "source_revision": run.get("source_revision"),
        "counts": counts,
        "trace_count": len(trace_ids),
        "event_counts": dict(sorted(event_counts.items())),
        "problems": problems,
    }


def audit(root: str | Path = ".") -> dict[str, Any]:
    """Return a JSON-ready audit snapshot without network or provider calls."""

    root = Path(root).resolve()
    artifacts = {
        key: _artifact_audit(root, artifact, version, scheduled)
        for key, (artifact, version, scheduled) in REQUIRED_RUNS.items()
    }
    manifest_path = root / "evidence" / "MANIFEST.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            value = _json(manifest_path)
            manifest = value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            manifest = {}

    current_v2_hash = _sha256(root / "prompts" / "v2.txt") if (root / "prompts" / "v2.txt").exists() else None
    manifest_v2_hash = next(
        (item.get("sha256") for item in manifest.get("files", []) if item.get("path") == "prompts/v2.txt"),
        None,
    )
    head = _git(root, "rev-parse", "HEAD")
    freeze = _git(root, "rev-parse", "v1-frozen")
    v1_revision = artifacts["v1_development"].get("source_revision")
    v2_revision = artifacts["v2_development"].get("source_revision")
    comparability_problems = []
    if v1_revision != v2_revision:
        comparability_problems.append("v1 and v2 development source revisions differ")
    if freeze and v2_revision != freeze:
        comparability_problems.append("v2 development source revision is not the frozen tag")
    if current_v2_hash != manifest_v2_hash:
        comparability_problems.append("working-tree prompts/v2.txt differs from manifest hash")

    status_output = _git(root, "status", "--short", "--untracked-files=all") or ""
    dirty_paths = [line[3:] for line in status_output.splitlines() if len(line) >= 4]
    late_candidate = root / "artifacts" / "v1-development-final-turn-gated"
    late_candidate_status = "unverified"
    if not late_candidate.exists():
        late_candidate_status = "not-present"
    elif (late_candidate / "trace_verification.json").exists():
        late_candidate_status = "complete"

    return {
        "root": str(root),
        "head": head,
        "freeze_revision": freeze,
        "manifest_commit": manifest.get("commit"),
        "dirty_path_count": len(dirty_paths),
        "dirty_path_sample": dirty_paths[:20],
        "required_artifacts": artifacts,
        "checks": {
            "required_artifact_receipts": {
                "status": "complete" if all(item["status"] == "complete" for item in artifacts.values()) else "incomplete",
                "evidence": "artifacts/v1-development-rerun, artifacts/v2-development, artifacts/v1-check, artifacts/v2-check",
            },
            "trace_diagnostics": {
                "status": "complete"
                if all(
                    item["event_counts"].get("provider_attempt", 0) >= 1
                    and item["event_counts"].get("answer_diagnostics", 0) == item["counts"].get("scheduled")
                    for item in artifacts.values()
                )
                else "incomplete",
                "reason": "the four designated receipts predate provider_attempt/answer_diagnostics recording"
                if not all(
                    item["event_counts"].get("provider_attempt", 0) >= 1
                    and item["event_counts"].get("answer_diagnostics", 0) == item["counts"].get("scheduled")
                    for item in artifacts.values()
                )
                else None,
            },
            "development_comparability": {
                "status": "complete" if not comparability_problems else "incomplete",
                "v1_source_revision": v1_revision,
                "v2_source_revision": v2_revision,
                "freeze_revision": freeze,
                "problems": comparability_problems,
            },
            "manifest_matches_worktree": {
                "status": "complete" if manifest.get("commit") == head and current_v2_hash == manifest_v2_hash else "incomplete",
                "manifest_commit": manifest.get("commit"),
                "head": head,
                "manifest_v2_hash": manifest_v2_hash,
                "current_v2_hash": current_v2_hash,
            },
            "late_candidate_receipt": {
                "status": late_candidate_status,
                "artifact": str(late_candidate),
                "reason": "the later candidate has no trace_verification.json receipt" if late_candidate_status == "unverified" else None,
            },
        },
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2, sort_keys=True))
