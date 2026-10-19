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
    "v1_development": ("v1-development-corrected-final", "v1", 40),
    "v2_development": ("v2-development-corrected-final", "v2", 40),
    "v1_check": ("v1-check-corrected-final", "v1", 10),
    "v2_check": ("v2-check-corrected-final", "v2", 10),
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


def _manifest_file_audit(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Check the selected manifest scope without claiming whole-tree coverage."""

    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    entries = manifest.get("files", [])
    if not isinstance(entries, list):
        return {
            "status": "incomplete",
            "entry_count": 0,
            "missing": [],
            "mismatches": [],
            "problems": ["manifest files field is not a list"],
        }
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            mismatches.append({"path": None, "problem": "invalid manifest file entry"})
            continue
        relative = item["path"]
        path = root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        actual_bytes = path.stat().st_size
        actual_sha = _sha256(path)
        if item.get("bytes") != actual_bytes or item.get("sha256") != actual_sha:
            mismatches.append(
                {
                    "path": relative,
                    "manifest_bytes": item.get("bytes"),
                    "actual_bytes": actual_bytes,
                    "manifest_sha256": item.get("sha256"),
                    "actual_sha256": actual_sha,
                }
            )
    problems = []
    if missing:
        problems.append(f"manifest paths missing: {len(missing)}")
    if mismatches:
        problems.append(f"manifest hashes or sizes differ: {len(mismatches)}")
    return {
        "status": "complete" if not problems else "incomplete",
        "entry_count": len(entries),
        "missing": missing,
        "mismatches": mismatches,
        "problems": problems,
    }


def _artifact_audit(root: Path, name: str, version: str, scheduled: int) -> dict[str, Any]:
    directory = root / "artifacts" / name
    relative_directory = directory.relative_to(root).as_posix()
    problems: list[str] = []
    missing = [filename for filename in REQUIRED_ARTIFACT_FILES if not (directory / filename).exists()]
    if missing:
        return {"status": "incomplete", "artifact": relative_directory, "missing": missing, "problems": missing}

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
            "artifact": relative_directory,
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
        "artifact": relative_directory,
        "split": run.get("split"),
        "agent_version": run.get("agent_version"),
        "run_id": run.get("run_id"),
        "source_revision": run.get("source_revision"),
        "prompt_sha256": (
            run.get("file_hashes", {}).get(f"prompts/{version}.txt")
            if isinstance(run.get("file_hashes"), dict)
            else None
        ),
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
    manifest_tags = manifest.get("tags") if isinstance(manifest.get("tags"), dict) else {}
    v1_freeze = _git(root, "rev-parse", "v1-final-frozen") or manifest_tags.get("v1-final-frozen")
    v2_freeze = _git(root, "rev-parse", "v2-final-frozen") or manifest_tags.get("v2-final-frozen")
    v1_revision = artifacts["v1_development"].get("source_revision")
    v2_revision = artifacts["v2_development"].get("source_revision")
    v1_prompt = artifacts["v1_development"].get("prompt_sha256")
    v2_prompt = artifacts["v2_development"].get("prompt_sha256")
    comparability_problems = []
    clean_corrected_rerun = (
        v1_revision is not None
        and v1_revision == v2_revision
        and v1_prompt is not None
        and v2_prompt is not None
        and v1_prompt != v2_prompt
    )
    if not clean_corrected_rerun:
        if v1_freeze and v1_revision != v1_freeze:
            comparability_problems.append("v1 development source revision is not v1-final-frozen")
        if v2_freeze and v2_revision != v2_freeze:
            comparability_problems.append("v2 development source revision is not v2-final-frozen")
        if v1_revision == v2_revision:
            comparability_problems.append("v1 and v2 development source revisions should differ")
    if current_v2_hash != manifest_v2_hash:
        comparability_problems.append("working-tree prompts/v2.txt differs from manifest hash")

    try:
        status_result = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        # Do not strip this output: Git's two-character status prefix is
        # meaningful for the first line as well as subsequent lines.
        status_output = status_result.stdout
    except (OSError, subprocess.SubprocessError):
        status_output = ""
    dirty_paths = [line[3:] for line in status_output.splitlines() if len(line) >= 4]
    late_candidate = root / "artifacts" / "v1-development-corrected-final"
    late_candidate_status = "designated"
    if not late_candidate.exists():
        late_candidate_status = "not-present"

    manifest_files = _manifest_file_audit(root, manifest)
    manifest_commit = manifest.get("generated_for_commit", manifest.get("commit"))
    manifest_problems = list(manifest_files["problems"])
    if head and manifest_commit != head:
        manifest_problems.append("manifest generated_for_commit differs from current HEAD")

    final_prompt_hashes = {
        key: next(
            (
                item.get("sha256")
                for item in manifest.get("files", [])
                if isinstance(item, dict) and item.get("path") == path
            ),
            None,
        )
        for key, path in (("v1", "prompts/v1.txt"), ("v2", "prompts/v2.txt"))
    }
    current_prompt_hashes = {
        key: _sha256(root / path) if (root / path).is_file() else None
        for key, path in (("v1", "prompts/v1.txt"), ("v2", "prompts/v2.txt"))
    }
    artifact_prompt_hashes = {
        key: artifacts[f"{key}_development"].get("prompt_sha256")
        for key in ("v1", "v2")
    }
    prompt_provenance = {
        "status": "frozen_and_current_match"
        if current_prompt_hashes == artifact_prompt_hashes
        else "current_source_differs_from_frozen_run",
        "frozen_run_hashes": artifact_prompt_hashes,
        "manifest_hashes": final_prompt_hashes,
        "current_worktree_hashes": current_prompt_hashes,
        "note": "A current prompt hash differing from a saved run is disclosed provenance, not a hash fabrication or a reason to rewrite the run.",
    }

    verification_error_files = [
        artifact
        for artifact, _version, _scheduled in REQUIRED_RUNS.values()
        if (root / "artifacts" / artifact / "trace_verification_error.json").is_file()
    ]
    receipt_complete = all(
        item["status"] == "complete" for item in artifacts.values()
    )
    diagnostics_complete = all(
        item["event_counts"].get("provider_attempt", 0) >= 1
        and item["event_counts"].get("answer_diagnostics", 0) == item["counts"].get("scheduled")
        for item in artifacts.values()
    )

    return {
        "root": ".",
        "head": head,
        "freeze_revision": {"v1": v1_freeze, "v2": v2_freeze},
        "manifest_commit": manifest_commit,
        "dirty_path_count": len(dirty_paths),
        "dirty_path_sample": dirty_paths[:20],
        "required_artifacts": artifacts,
        "checks": {
            "required_artifact_receipts": {
                "status": "complete" if receipt_complete else "incomplete",
                "evidence": "artifacts/v1-development-corrected-final, artifacts/v2-development-corrected-final, artifacts/v1-check-corrected-final, artifacts/v2-check-corrected-final",
            },
            "trace_diagnostics": {
                "status": "complete" if diagnostics_complete and receipt_complete else "incomplete",
                "receipt_status": "complete" if receipt_complete else "incomplete",
                "event_diagnostics_status": "complete" if diagnostics_complete else "incomplete",
                "verification_process_status": "incomplete" if verification_error_files else "complete",
                "verification_error_files": verification_error_files,
                "reason": (
                    "a designated run wrote trace_verification_error.json even though its saved receipt rows are present"
                    if verification_error_files
                    else "one or more designated receipts lack provider_attempt/answer_diagnostics recording"
                    if not diagnostics_complete
                    else None
                ),
            },
            "development_comparability": {
                "status": "complete" if not comparability_problems else "incomplete",
                "v1_source_revision": v1_revision,
                "v2_source_revision": v2_revision,
                "v1_prompt_sha256": v1_prompt,
                "v2_prompt_sha256": v2_prompt,
                "mode": "clean_corrected_rerun" if clean_corrected_rerun else "historical_frozen_runs",
                "freeze_revision": {"v1": v1_freeze, "v2": v2_freeze},
                "problems": comparability_problems,
            },
            "manifest_matches_worktree": {
                "status": "complete" if not manifest_problems else "incomplete",
                "manifest_commit": manifest_commit,
                "head": head,
                "manifest_v2_hash": manifest_v2_hash,
                "current_v2_hash": current_v2_hash,
                "selected_file_scope": manifest_files,
                "problems": manifest_problems,
            },
            "prompt_provenance": prompt_provenance,
            "historical_artifact_policy": {
                "status": "complete",
                "current_artifacts": [
                    f"artifacts/{REQUIRED_RUNS['v1_development'][0]}",
                    f"artifacts/{REQUIRED_RUNS['v2_development'][0]}",
                    f"artifacts/{REQUIRED_RUNS['v1_check'][0]}",
                    f"artifacts/{REQUIRED_RUNS['v2_check'][0]}",
                ],
                "note": "All other artifact directories are retained historical or exploratory evidence and are excluded from the final comparison.",
            },
            "late_candidate_receipt": {
                "status": late_candidate_status,
                "artifact": late_candidate.relative_to(root).as_posix(),
                "reason": "This directory is the designated v1 development artifact; it is no longer treated as an unverified late candidate." if late_candidate_status == "designated" else None,
            },
        },
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2, sort_keys=True))
