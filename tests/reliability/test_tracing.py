from __future__ import annotations

import asyncio
import json

from claimlens.tracing import LangfuseTracer


def test_langfuse_verifier_uses_explicit_run_window_without_network() -> None:
    tracer = LangfuseTracer(public_key="public", secret_key="secret", host="https://example.test")
    captured: dict[str, str] = {}

    def request(method: str, path: str):
        captured["method"] = method
        captured["path"] = path
        return 200, json.dumps(
            {
                "data": [
                    {"name": "claimlens.answer", "type": "SPAN"},
                    {"name": "get_brand_facts", "type": "TOOL"},
                    {"name": "model-turn-1", "type": "GENERATION"},
                ]
            }
        ).encode()

    tracer._request_with_status = request  # type: ignore[method-assign]
    result = asyncio.run(
        tracer.verify_trace(
            "trace-1",
            from_start_time="2026-10-20T00:00:00+00:00",
            to_start_time="2026-10-20T00:05:00+00:00",
        )
    )

    assert result["data"][1]["name"] == "get_brand_facts"
    assert captured["method"] == "GET"
    assert "traceId=trace-1" in captured["path"]
    assert "fromStartTime=2026-10-20T00%3A00%3A00Z" in captured["path"]
    assert "toStartTime=2026-10-20T00%3A05%3A00Z" in captured["path"]
