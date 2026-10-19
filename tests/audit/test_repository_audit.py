from __future__ import annotations

import csv
import json
from pathlib import Path

from agent.core import _retryable
from claimlens.contracts import ProviderAuthError
from claimlens.loaders import load_answers, load_facts
from evidence.audit.repository_audit import audit


ROOT = Path(__file__).resolve().parents[2]


def test_required_live_artifact_receipts_have_terminal_accounting() -> None:
    snapshot = audit(ROOT)

    assert snapshot["checks"]["required_artifact_receipts"]["status"] == "complete"
    for item in snapshot["required_artifacts"].values():
        assert item["status"] == "complete", item["problems"]


def test_audit_matches_final_freezes_and_reports_manifest_state() -> None:
    snapshot = audit(ROOT)

    comparison = snapshot["checks"]["development_comparability"]
    assert comparison["status"] == "complete"
    assert comparison["v1_source_revision"] == comparison["v2_source_revision"]
    assert comparison["v1_prompt_sha256"] != comparison["v2_prompt_sha256"]

    manifest = snapshot["checks"]["manifest_matches_worktree"]
    assert manifest["manifest_v2_hash"]
    assert manifest["current_v2_hash"]

    assert snapshot["checks"]["trace_diagnostics"]["status"] == "complete"


def test_loader_preserves_quoted_commas_embedded_newlines_and_typed_booleans(tmp_path: Path) -> None:
    answers_path = tmp_path / "answers.csv"
    with answers_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["answer_id", "split", "brand_id", "question", "ai_answer"])
        writer.writerow(["A01", "development", "brand-a", "What?", 'First line, "quoted".\nSecond line.'])

    facts_path = tmp_path / "brand_facts.csv"
    with facts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "fact_id",
                "brand_id",
                "model_id",
                "field",
                "value",
                "value_type",
                "unit",
                "condition",
                "valid_from",
                "valid_to",
            ]
        )
        writer.writerow(["fact-1", "brand-a", "model-a", "wifi", "false", "boolean", "", "", "", ""])

    answers = load_answers(answers_path)
    facts = load_facts(facts_path)

    assert answers[0].ai_answer == 'First line, "quoted".\nSecond line.'
    assert facts[0].value is False
    assert facts[0].value_type == "boolean"


def test_selective_retry_classifies_rate_limits_but_not_authentication() -> None:
    class RateLimitedError(Exception):
        status_code = 429

    assert _retryable(RateLimitedError()) is True
    assert _retryable(ProviderAuthError("bad key")) is False


def test_gold_is_joined_after_inference_and_not_imported_by_agent() -> None:
    agent_source = (ROOT / "agent" / "core.py").read_text(encoding="utf-8")
    runner_source = (ROOT / "runner" / "experiment.py").read_text(encoding="utf-8")

    assert "development_gold" not in agent_source
    assert "from harness" not in agent_source
    assert "load_gold" in runner_source
    assert runner_source.index("outcomes = await executor.run") < runner_source.index("load_gold(")


def test_check_reports_redact_gold_derived_quality_fields() -> None:
    for artifact in ("v1-check-final", "v2-check-final"):
        report = json.loads((ROOT / "artifacts" / artifact / "report.json").read_text(encoding="utf-8"))
        metrics = report["metrics"]
        assert report["labeled"] is False
        assert report["gold_claims"] == []
        assert report["matches"] == []
        assert all(
            metrics[field] is None
            for field in (
                "gold_claims",
                "matched_spans",
                "correct_labels",
                "extraction_f1",
                "joint_f1",
                "macro_f1",
            )
        )
