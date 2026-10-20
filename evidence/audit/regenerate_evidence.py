"""Regenerate selected summary and provenance evidence without provider calls.

This script reads saved artifacts, the local assessment inputs, and Git metadata.
It never starts an API, database, model, or Langfuse request.  The manifest is
deliberately a selected-evidence manifest; it is not a hash of the whole tree.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from harness.comparison import compare_versions


ROOT = Path(__file__).resolve().parents[2]
FINAL_RUNS = {
    "v1-development": "v1-development-corrected-final",
    "v2-development": "v2-development-corrected-final",
    "v1-check": "v1-check-corrected-final",
    "v2-check": "v2-check-corrected-final",
}
DEVELOPMENT_RUNS = ("v1-development", "v2-development")
CHECK_RUNS = ("v1-check", "v2-check")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _run_payload(name: str) -> dict[str, Any]:
    artifact_name = FINAL_RUNS[name]
    directory = ROOT / "artifacts" / artifact_name
    run = _read_json(directory / "run.json")
    report = _read_json(directory / "report.json")
    outcomes = _read_json(directory / "outcomes.json")
    receipts = _read_json(directory / "trace_verification.json")
    file_hashes = run.get("file_hashes", {})
    observation_counts = [
        int(item.get("observation_count", 0))
        for item in receipts
        if isinstance(item, dict) and isinstance(item.get("observation_count"), int)
    ]
    errors = [
        {
            "answer_id": item.get("answer_id"),
            "error_type": item.get("error_type"),
            "error_message": item.get("error_message"),
        }
        for item in outcomes
        if isinstance(item, dict) and item.get("status") == "error"
    ]
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    return {
        "name": name,
        "artifact": f"artifacts/{artifact_name}",
        "run_id": run.get("run_id"),
        "prompt_version": run.get("prompt_version"),
        "source_revision": run.get("source_revision"),
        "split": run.get("split"),
        "counts": run.get("counts", {}),
        "metrics": metrics,
        "errors": errors,
        "settings": run.get("settings", {}),
        "prompt_sha256": next(
            (value for key, value in file_hashes.items() if key.startswith("prompts/")),
            None,
        ),
        "trace_receipts": len(receipts),
        "trace_observation_min": min(observation_counts) if observation_counts else None,
        "trace_observation_max": max(observation_counts) if observation_counts else None,
        "trace_observations_all_nonempty": bool(observation_counts)
        and all(value > 0 for value in observation_counts),
        "trace_verification_error": (directory / "trace_verification_error.json").is_file(),
    }


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    revisions = {row["name"]: row["source_revision"] for row in runs}
    prompt_hashes = {row["name"]: row["prompt_sha256"] for row in runs}
    return {
        "schema_version": "claimlens-final-run-summary-v2",
        "generated_for_commit": _git("rev-parse", "HEAD"),
        "runs": runs,
        "provenance": {
            "development_source_revisions": {
                name: revisions[name] for name in DEVELOPMENT_RUNS
            },
            "check_source_revisions": {name: revisions[name] for name in CHECK_RUNS},
            "prompt_hashes_by_run": prompt_hashes,
            "note": (
                "These corrected v1/v2 development runs share one post-fix source revision "
                "and differ only by prompt version. Both final checks were run from the same "
                "corrected source revision. Current HEAD may be later due to evidence-only changes."
            ),
        },
        "comparison": "evidence/v1-v2-final-comparison.json",
        "v1_analysis": "evidence/analysis/v1-development-final-turn-gated-analysis.json",
    }


def _claim_text_type(claim: dict[str, Any]) -> str:
    text = f"{claim.get('reason', '')} {claim.get('quote', '')}".lower()
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
    if claim.get("label") == "unknown":
        return "unsupported_or_unresolved_fact"
    return f"factual_{claim.get('label')}"


def _advice_counts(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    claims = {
        claim.get("claim_id"): claim
        for outcome in report.get("answer_outcomes", [])
        if isinstance(outcome, dict)
        for claim in outcome.get("claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    }
    unmatched = metrics.get("unmatched_prediction_ids", [])
    counts = Counter(
        _claim_text_type(claims[claim_id])
        for claim_id in unmatched
        if claim_id in claims
    )
    return {
        "unmatched_prediction_count": len(unmatched),
        "advice_or_instruction_only": counts.get("advice_or_instruction", 0),
        "meta_instruction": counts.get("meta_instruction", 0),
        "advice_or_meta_total": counts.get("advice_or_instruction", 0)
        + counts.get("meta_instruction", 0),
        "other_unmatched_by_heuristic": {
            key: value
            for key, value in sorted(counts.items())
            if key not in {"advice_or_instruction", "meta_instruction"}
        },
        "method": "saved reason/quote text bucket; descriptive diagnostic, not a gold metric",
    }


def _prediction_decomposition(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    v1 = {
        row["answer_id"]: row
        for row in reports["v1-development"].get("answer_scores", [])
    }
    v2 = {
        row["answer_id"]: row
        for row in reports["v2-development"].get("answer_scores", [])
    }
    rows = []
    for answer_id in sorted(set(v1) | set(v2)):
        before = v1.get(answer_id, {})
        after = v2.get(answer_id, {})
        before_count = int(before.get("predictions") or 0)
        after_count = int(after.get("predictions") or 0)
        if before_count != after_count or before.get("status") != after.get("status"):
            rows.append(
                {
                    "answer_id": answer_id,
                    "v1_predictions": before_count,
                    "v2_predictions": after_count,
                    "delta": after_count - before_count,
                    "v1_status": before.get("status"),
                    "v2_status": after.get("status"),
                    "v1_error_type": before.get("error_type"),
                    "v2_error_type": after.get("error_type"),
                }
            )
    return {
        "v1_total": sum(int(row.get("predictions") or 0) for row in v1.values()),
        "v2_total": sum(int(row.get("predictions") or 0) for row in v2.values()),
        "net_delta": sum(int(row.get("predictions") or 0) for row in v2.values())
        - sum(int(row.get("predictions") or 0) for row in v1.values()),
        "answer_deltas": rows,
    }


def _flip_provenance(comparison: dict[str, Any]) -> dict[str, Any]:
    table = comparison["claim_flip_table"]
    fixed = list(table["fixed_claim_ids"])
    regressed = list(table["regressed_claim_ids"])
    error_fixed_prefixes = ("A04:", "A22:")
    error_regressed_prefixes = ("A25:", "A34:")
    error_driven_fixed = [item for item in fixed if item.startswith(error_fixed_prefixes)]
    genuine_fixed = [item for item in fixed if item not in error_driven_fixed]
    error_driven_regressed = [
        item for item in regressed if item.startswith(error_regressed_prefixes)
    ]
    genuine_regressed = [item for item in regressed if item not in error_driven_regressed]
    other_error_changes = {
        "missed_to_wrong_label": [],
        "wrong_label_to_missed": [],
    }
    for claim_id, states in table["states"].items():
        transition = (states.get("v1"), states.get("v2"))
        if transition == ("missed", "wrong_label"):
            other_error_changes["missed_to_wrong_label"].append(claim_id)
        elif transition == ("wrong_label", "missed"):
            other_error_changes["wrong_label_to_missed"].append(claim_id)
    return {
        "fixed_count": len(fixed),
        "regressed_count": len(regressed),
        "error_driven_fixed_ids": error_driven_fixed,
        "genuine_fixed_ids": genuine_fixed,
        "error_driven_regressed_ids": error_driven_regressed,
        "genuine_regressed_ids": genuine_regressed,
        "other_error_driven_state_changes": other_error_changes,
        "definitions": {
            "error_driven_fixed": "claims in A04/A22 where v1 was a provider timeout and v2 produced a terminal result",
            "error_driven_regressed": "claims in A25/A34 where v2 became an invalid-output answer error",
            "genuine": "remaining fixed/regressed IDs whose answer-level status did not change between runs",
        },
    }


def _captured_check(path: str, command: str) -> dict[str, Any]:
    """Summarize a captured command transcript without copying its output."""

    file_path = ROOT / path
    if not file_path.is_file():
        return {
            "command": command,
            "output": path,
            "status": "unrecorded",
        }
    lines = file_path.read_text(encoding="utf-8").splitlines()
    exit_lines = [line for line in lines if line.startswith("exit=")]
    exit_code: int | None = None
    if exit_lines:
        try:
            exit_code = int(exit_lines[-1].split("=", 1)[1])
        except ValueError:
            exit_code = None
    summaries = [
        line.strip()
        for line in lines
        if " passed" in line or " passed," in line or "status" in line and "verified" in line
    ]
    return {
        "command": command,
        "output": path,
        "exit_code": exit_code,
        "status": "passed" if exit_code == 0 else "failed" if exit_code is not None else "unknown",
        "summary_lines": summaries[-3:],
    }


def _provenance_payload(
    summary: dict[str, Any], comparison: dict[str, Any], reports: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    canaries = {
        name: _read_json(ROOT / f"evidence/{name}-final-injection-canary.json")
        for name in ("v1", "v2")
    }
    selected_trace = _read_json(ROOT / "evidence/trace-diagnosis.json")
    investigation = _read_json(ROOT / "evidence/langfuse-investigation/report.json")
    langfuse_summary = _read_json(ROOT / "evidence/langfuse/langfuse-summary.json")
    return {
        "schema_version": "claimlens-submission-provenance-v1",
        "generated_for_commit": summary["generated_for_commit"],
        "final_run_summary": "evidence/final-run-summary.json",
        "comparison": "evidence/v1-v2-final-comparison.json",
        "source_revisions": {
            row["name"]: row["source_revision"] for row in summary["runs"]
        },
        "source_revision_interpretation": {
            "development": "Corrected v1/v2 runs share one post-fix source revision; the prompt version is the controlled model-behavior change.",
            "checks": "Both corrected final-check artifacts use the same post-fix source revision as development.",
            "historical_runs": "The prior frozen-tag runs remain preserved under their original artifact directories.",
            "current_head": "Current committed HEAD may be later than the live runs because evidence-only changes do not alter their provenance.",
        },
        "prediction_count_decomposition": _prediction_decomposition(reports),
        "advice_instruction_diagnostics": {
            "v1": _advice_counts(reports["v1-development"]),
            "v2": _advice_counts(reports["v2-development"]),
            "v1_formal_analysis": {
                "unmatched_predictions": 261,
                "advice_or_instruction_only": 254,
                "meta_instruction": 3,
                "source": "evidence/analysis/v1-development-final-turn-gated-analysis.json",
            },
            "interpretation": "The v1 254 count is the saved offline diagnosis. The v2 bucket is a text/reason heuristic and is reported separately, not as a gold-derived score.",
        },
        "claim_flip_provenance": _flip_provenance(comparison),
        "error_outcomes": {
            row["name"]: row["errors"] for row in summary["runs"]
        },
        "injection_canaries": {
            name: {
                "passed": payload.get("passed"),
                "answer_ids": payload.get("answer_ids"),
                "rows": [
                    {
                        "answer_id": row.get("answer_id"),
                        "status": row.get("status"),
                        "marker_extracted": row.get("marker_extracted"),
                        "all_extracted_labels_supported": row.get(
                            "all_extracted_labels_supported"
                        ),
                        "override_suspected": row.get("override_suspected"),
                        "passed": row.get("passed"),
                        "predictions": row.get("predictions"),
                    }
                    for row in payload.get("canaries", [])
                ],
            }
            for name, payload in canaries.items()
        },
        "langfuse_limitations": {
            "saved_receipt_counts": {
                row["name"]: {
                    "trace_receipts": row["trace_receipts"],
                    "all_observation_counts_nonempty": row[
                        "trace_observations_all_nonempty"
                    ],
                    "verification_error_file": row["trace_verification_error"],
                }
                for row in summary["runs"]
            },
            "selected_trace_diagnosis": selected_trace,
            "investigation_conclusion": investigation.get("conclusion"),
            "remote_confirmation_counts": langfuse_summary.get(
                "remote_confirmation_counts", {}
            ),
            "remote_requery": langfuse_summary.get("query_policy", {}),
            "limitation": (
                "The later serial re-query confirmed all 100 current trace IDs and "
                "retained sanitized observation metadata. Full reviewer-facing remote "
                "payload exports remain incomplete; prompts, model/tool payloads, score "
                "payloads, credentials, and raw response bodies are not exported."
            ),
        },
        "current_checks": {
            "make_test": _captured_check("evidence/pytest-output.txt", "make test"),
            "make_verify": _captured_check("evidence/verify-output.txt", "make verify"),
        },
        "offline_generation": True,
        "provider_experiments_started": False,
    }


def _manifest_paths(previous: dict[str, Any]) -> list[str]:
    paths = {
        item.get("path")
        for item in previous.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    paths.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "artifacts").rglob("*")
        if path.is_file()
    )
    for directory in (
        ROOT / "evidence" / "audit",
        ROOT / "evidence" / "analysis",
        ROOT / "evidence" / "submission",
        ROOT / "evidence" / "diagnosis",
        ROOT / "evidence" / "exchanges",
        ROOT / "evidence" / "langfuse",
        ROOT / "evidence" / "supplementary",
    ):
        if directory.exists():
            paths.update(
                path.relative_to(ROOT).as_posix() for path in directory.rglob("*") if path.is_file()
            )
    paths.update(
        (
            "evidence/audit-notes.md",
            "evidence/run-smoke.txt",
            "prompts/v1.txt",
            "prompts/v2.txt",
        )
    )
    paths.discard("evidence/MANIFEST.json")
    # The audit snapshot reports the manifest's state and therefore cannot be
    # hashed inside that same manifest without creating a self-reference.
    paths.discard("evidence/audit/current-audit.json")
    return sorted(path for path in paths if (ROOT / path).is_file())


def _manifest(summary: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    manifest_path = ROOT / "evidence" / "MANIFEST.json"
    previous = _read_json(manifest_path) if manifest_path.exists() else {}
    files = [
        {"path": path, "bytes": (ROOT / path).stat().st_size, "sha256": _sha256(ROOT / path)}
        for path in _manifest_paths(previous)
    ]
    run_by_name = {row["name"]: row for row in summary["runs"]}
    current_prompts = {
        path: _sha256(ROOT / path) for path in ("prompts/v1.txt", "prompts/v2.txt")
    }
    csv_hashes = {
        path: _sha256(ROOT / path)
        for path in ("answers.csv", "brand_facts.csv", "development_gold.csv")
        if (ROOT / path).is_file()
    }
    tags = {
        tag: _git("rev-parse", tag)
        for tag in ("v1-final-frozen", "v2-final-frozen", "v1-frozen", "v2-frozen")
        if _git("rev-parse", tag)
    }
    return {
        "schema_version": "claimlens-manifest-v3",
        "generated_for_commit": summary["generated_for_commit"],
        "working_tree_status": "generated_snapshot",
        "coverage": {
            "included": [
                "all currently existing files below artifacts/",
                "selected historical/current files previously listed under evidence/",
                "evidence/audit/**, evidence/analysis/**, and evidence/submission/**",
                "evidence/diagnosis/**, evidence/exchanges/**, evidence/langfuse/**, and evidence/supplementary/**",
                "evidence/audit-notes.md and evidence/run-smoke.txt",
                "prompts/v1.txt and prompts/v2.txt",
                "SHA-256 values for local answers.csv, brand_facts.csv, and development_gold.csv when present",
            ],
            "excluded": [
                "source code and tests outside the selected evidence scope",
                "README.md, HANDOFF.md, Assessment.docx, .env, and virtual-environment files",
                "untracked files outside the selected evidence scope",
                "evidence/audit/current-audit.json (derived snapshot; self-referential if hashed)",
            ],
            "claim": "This is a selected-evidence manifest, not full repository-tree coverage.",
        },
        "csv_sha256": csv_hashes,
        "prompt_sha256": {
            "prompts/v1.txt": run_by_name["v1-development"]["prompt_sha256"],
            "prompts/v2.txt": run_by_name["v2-development"]["prompt_sha256"],
        },
        "current_committed_prompt_sha256": current_prompts,
        "files": files,
        "final_runs": {
            name: run_by_name[name]["run_id"] for name in FINAL_RUNS
        },
        "source_revisions": {
            name: run_by_name[name]["source_revision"] for name in FINAL_RUNS
        },
        "tags": tags,
        "historical_status": {
            "artifact_policy": "Only the four final artifact directories named by final_runs feed final-run-summary.json and v1-v2-final-comparison.json. Other artifact directories are retained historical/exploratory evidence.",
            "superseded_evidence": {
                "evidence/run-summary.json": "historical zero-prediction/early sequence; superseded by evidence/final-run-summary.json",
                "evidence/v1-v2-comparison.json": "historical comparison; superseded by evidence/v1-v2-final-comparison.json",
                "evidence/v1-injection-canary.json": "historical canary output; final counterpart is evidence/v1-final-injection-canary.json",
                "evidence/v2-injection-canary.json": "historical canary output; final counterpart is evidence/v2-final-injection-canary.json",
            },
        },
        "comparison": {
            "path": "evidence/v1-v2-final-comparison.json",
            "joint_f1_delta": comparison.get("joint_f1_delta"),
        },
    }


def main() -> int:
    runs = [_run_payload(name) for name in FINAL_RUNS]
    reports = {
        name: _read_json(ROOT / "artifacts" / artifact / "report.json")
        for name, artifact in FINAL_RUNS.items()
    }
    summary = _summary(runs)
    _write_json(ROOT / "evidence" / "final-run-summary.json", summary)

    comparison_object = compare_versions(
        ROOT / "artifacts" / FINAL_RUNS["v1-development"] / "report.json",
        ROOT / "artifacts" / FINAL_RUNS["v2-development"] / "report.json",
    )
    comparison = comparison_object.as_dict()
    comparison["schema_version"] = "claimlens-comparison-report-v1"
    comparison["metadata"] = {
        "hypothesis": "exclude advice/instruction/opinion-only sentences",
            "v1_report": f"artifacts/{FINAL_RUNS['v1-development']}/report.json",
            "v2_report": f"artifacts/{FINAL_RUNS['v2-development']}/report.json",
        "v1_source_revision": runs[0]["source_revision"],
        "v2_source_revision": runs[1]["source_revision"],
        "offline_regeneration": True,
    }
    _write_json(ROOT / "evidence" / "v1-v2-final-comparison.json", comparison)

    provenance = _provenance_payload(summary, comparison, reports)
    _write_json(ROOT / "evidence" / "submission" / "final-provenance.json", provenance)

    # Re-read after writing the summaries so their actual bytes/hashes are used.
    manifest = _manifest(summary, comparison)
    _write_json(ROOT / "evidence" / "MANIFEST.json", manifest)
    print(
        json.dumps(
            {
                "offline": True,
                "generated_for_commit": summary["generated_for_commit"],
                "manifest_entries": len(manifest["files"]),
                "runs": {row["name"]: row["counts"] for row in runs},
                "joint_f1_delta": comparison["joint_f1_delta"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
