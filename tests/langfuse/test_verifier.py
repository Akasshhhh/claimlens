from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from claimlens.tracing import observation_query, observation_window
from runner.experiment import _verify_traces


def _audit_module():
    path = Path(__file__).resolve().parents[2] / "evidence" / "langfuse-investigation" / "audit.py"
    spec = importlib.util.spec_from_file_location("claimlens_langfuse_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observation_window_is_explicit_and_utc() -> None:
    assert observation_window(
        "2026-10-20T10:00:00+05:30",
        "2026-10-20T10:02:00+05:30",
    ) == (
        "2026-10-20T04:29:00Z",
        "2026-10-20T04:33:00Z",
    )


def test_observation_query_uses_per_answer_id_and_run_window() -> None:
    path = observation_query(
        "answer-trace-01",
        from_start_time="2026-10-20T04:29:00+00:00",
        to_start_time="2026-10-20T04:33:00+00:00",
    )
    query = parse_qs(urlsplit(path).query)

    assert query["traceId"] == ["answer-trace-01"]
    assert query["fromStartTime"] == ["2026-10-20T04:29:00Z"]
    assert query["toStartTime"] == ["2026-10-20T04:33:00Z"]
    assert query["limit"] == ["10"]
    assert query["fields"] == ["core,basic"]
    assert "runId" not in query


def test_trace_verification_errors_are_isolated_per_answer() -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    class FakeTracer:
        async def verify_trace(self, trace_id: str, **kwargs):
            calls.append((trace_id, kwargs.get("from_start_time"), kwargs.get("to_start_time")))
            if trace_id == "bad-trace":
                raise RuntimeError("secret_key=do-not-export")
            return {"data": [{"name": "claimlens.answer"}]}

    receipts = asyncio.run(
        _verify_traces(
            FakeTracer(),
            ["good-trace", "bad-trace", "other-good-trace"],
            spacing_seconds=0,
            from_start_time="2026-10-20T04:29:00Z",
            to_start_time="2026-10-20T04:33:00Z",
        )
    )

    assert [receipt["trace_id"] for receipt in receipts] == [
        "good-trace",
        "bad-trace",
        "other-good-trace",
    ]
    assert [receipt["status"] for receipt in receipts] == ["verified", "error", "verified"]
    assert receipts[1]["error_type"] == "RuntimeError"
    assert "do-not-export" not in receipts[1]["error_message"]
    assert calls == [
        ("good-trace", "2026-10-20T04:29:00Z", "2026-10-20T04:33:00Z"),
        ("bad-trace", "2026-10-20T04:29:00Z", "2026-10-20T04:33:00Z"),
        ("other-good-trace", "2026-10-20T04:29:00Z", "2026-10-20T04:33:00Z"),
    ]


def test_audit_summary_does_not_export_remote_payloads_or_scores() -> None:
    audit = _audit_module()
    raw = json.dumps(
        {
            "data": [
                {
                    "name": "claimlens.answer",
                    "input": "private answer text",
                    "output": {"claims": []},
                    "secret_key": "private-secret",
                    "score": 1.0,
                }
            ],
            "meta": {"projectId": "private-project"},
        }
    ).encode()

    summary = audit._body_observation_summary(200, raw)

    assert summary["observation_count"] == 1
    assert summary["observation_names"] == ["claimlens.answer"]
    assert "private answer text" not in json.dumps(summary)
    assert "private-secret" not in json.dumps(summary)
    assert "score" not in summary
    assert "input" not in summary
    assert summary["body_sha256"]


def test_offline_audit_records_blocker_without_network_or_model(tmp_path: Path) -> None:
    audit = _audit_module()
    output = tmp_path / "audit.json"

    report = audit.audit(output=output, offline=True)

    assert report["scope"]["provider_runs_started"] is False
    assert report["scope"]["model_calls_started"] == 0
    assert report["remote_verification"]["status"] == "blocked"
    assert "offline mode requested" in report["remote_verification"]["blockers"][0]
    assert report["full_exports"]["status"] == "not_collected"
    assert output.exists()
