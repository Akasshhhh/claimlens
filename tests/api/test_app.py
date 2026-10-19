from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from claimlens.api.app import create_app
from claimlens.contracts import (
    AgentOutput,
    Claim,
    ProviderTimeoutError,
    UnknownBrandError,
)


RUN_ID = "00000000-0000-0000-0000-000000000001"


class FakeRepo:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    def create_run(self, meta: dict) -> str:
        self.created.append(meta)
        return RUN_ID

    def save_answer_result(
        self,
        run_id: str,
        answer_id: str,
        status: str,
        error: dict | None,
        latency_ms: float | None,
        trace_ref: str | None,
        claims: list[dict],
    ) -> None:
        self.saved.append(
            {
                "run_id": run_id,
                "answer_id": answer_id,
                "status": status,
                "error": error,
                "latency_ms": latency_ms,
                "trace_ref": trace_ref,
                "claims": claims,
            }
        )

    def finish_run(
        self,
        run_id: str,
        status: str,
        counts: dict,
        metrics: dict | None,
    ) -> None:
        self.finished.append(
            {"run_id": run_id, "status": status, "counts": counts, "metrics": metrics}
        )

    def get_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, FakeRepo]] = []

    async def run_split(self, agent_version: str, split: str, repo: FakeRepo) -> dict[str, Any]:
        self.calls.append((agent_version, split, repo))
        return {
            "run_id": RUN_ID,
            "status": "completed",
            "counts": {"answers": 50, "errors": 1},
            "metrics": {"joint_f1": 0.75},
        }


class FakeAgent:
    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.calls: list[tuple[str, str]] = []

    async def classify(self, input_data, agent_version: str):
        self.calls.append((input_data.answer_id, agent_version))
        if self.behavior == "unknown_brand":
            raise UnknownBrandError("brand-secret")
        if self.behavior == "timeout":
            raise ProviderTimeoutError("secret timeout detail")
        if self.behavior == "provider":
            raise RuntimeError("sk-live-provider-secret")
        if self.behavior == "invalid":
            return {"answer_id": input_data.answer_id, "claims": [{"unexpected": "field"}]}
        if self.behavior == "malformed":
            return "not an AgentOutput"
        return AgentOutput(answer_id=input_data.answer_id, claims=[])


def _classify_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "answer_id": "A01",
        "brand_id": "cleartrail",
        "question": "Which speaker is this?",
        "ai_answer": "The speaker is compact.",
        "agent_version": "v1",
    }
    payload.update(overrides)
    return payload


def _client(*, agent: FakeAgent | None = None, repo: FakeRepo | None = None, runner: FakeRunner | None = None):
    return TestClient(
        create_app(
            repo=repo or FakeRepo(),
            runner=runner or FakeRunner(),
            agent=agent or FakeAgent(),
        )
    )


def test_classify_success_is_200_and_passes_version_to_agent() -> None:
    agent = FakeAgent()
    client = _client(agent=agent)

    response = client.post("/classify", json=_classify_payload(agent_version="v2"))

    assert response.status_code == 200
    assert response.json() == {"answer_id": "A01", "claims": []}
    assert agent.calls == [("A01", "v2")]


def test_classify_unknown_brand_is_404_with_sanitized_body() -> None:
    response = _client(agent=FakeAgent("unknown_brand")).post(
        "/classify", json=_classify_payload()
    )

    assert response.status_code == 404
    assert response.json() == {
        "type": "unknown_brand",
        "message": "unknown primary brand",
    }
    assert "brand-secret" not in response.text


def test_classify_provider_failure_is_502_without_provider_text() -> None:
    response = _client(agent=FakeAgent("provider")).post(
        "/classify", json=_classify_payload()
    )

    assert response.status_code == 502
    assert response.json() == {"type": "provider_failure", "message": "agent request failed"}
    assert "sk-live-provider-secret" not in response.text


def test_classify_invalid_output_is_502_without_raw_validation_details() -> None:
    response = _client(agent=FakeAgent("invalid")).post(
        "/classify", json=_classify_payload()
    )

    assert response.status_code == 502
    assert response.json() == {
        "type": "invalid_output",
        "message": "agent returned invalid output",
    }
    assert "unexpected" not in response.text


def test_classify_timeout_is_504_and_sanitized() -> None:
    response = _client(agent=FakeAgent("timeout")).post(
        "/classify", json=_classify_payload()
    )

    assert response.status_code == 504
    assert response.json() == {
        "type": "provider_timeout",
        "message": "provider request timed out",
    }
    assert "secret timeout detail" not in response.text


def test_classify_rejects_gold_claims_and_claim_list_fields() -> None:
    response = _client().post(
        "/classify",
        json=_classify_payload(gold_claims=[], claims=[]),
    )

    assert response.status_code == 422
    assert response.json() == {"type": "validation_error", "message": "invalid request"}


def test_classify_rejects_invalid_agent_version() -> None:
    response = _client().post("/classify", json=_classify_payload(agent_version="v3"))

    assert response.status_code == 422
    assert response.json()["type"] == "validation_error"


def test_evaluation_run_is_201_and_check_metrics_are_null() -> None:
    repo = FakeRepo()
    runner = FakeRunner()
    client = _client(repo=repo, runner=runner)

    response = client.post(
        "/evaluation-runs",
        json={"agent_version": "v2", "split": "check"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "run_id": RUN_ID,
        "status": "completed",
        "counts": {"answers": 50, "errors": 1},
        "metrics": None,
    }
    assert runner.calls == [("v2", "check", repo)]


def test_evaluation_run_persists_ok_empty_and_error_outcomes_via_repository() -> None:
    repo = FakeRepo()

    class OutcomeRunner:
        async def run_split(self, agent_version: str, split: str, repository: FakeRepo) -> dict[str, Any]:
            return {
                "run_id": RUN_ID,
                "status": "completed",
                "counts": {"scheduled": 3, "answers": 3, "ok": 2, "errors": 1, "predictions": 1},
                "metrics": {"joint_f1": 0.5},
                "scheduled_answer_ids": ["A01", "A02", "A03"],
                "answer_outcomes": [
                    {
                        "answer_id": "A01",
                        "status": "ok",
                        "latency_ms": 1,
                        "claims": [
                            {
                                "claim_id": "A01:0:4",
                                "start_char": 0,
                                "end_char": 4,
                                "quote": "Fact",
                                "label": "supported",
                                "fact_ids": [],
                                "reason": "The supplied row supports this.",
                            }
                        ],
                    },
                    {"answer_id": "A02", "status": "ok", "latency_ms": 1, "claims": []},
                    {
                        "answer_id": "A03",
                        "status": "error",
                        "latency_ms": 1,
                        "error_type": "provider_timeout",
                        "error_message": "provider request timed out",
                        "claims": [],
                    },
                ],
            }

    client = _client(repo=repo, runner=OutcomeRunner())
    response = client.post(
        "/evaluation-runs",
        json={"agent_version": "v1", "split": "development"},
    )

    assert response.status_code == 201
    assert [row["answer_id"] for row in repo.saved] == ["A01", "A02", "A03"]
    assert [row["status"] for row in repo.saved] == ["ok", "ok", "error"]
    assert repo.saved[1]["claims"] == []
    assert repo.saved[2]["error"] == {
        "type": "provider_timeout",
        "message": "provider request timed out",
    }


def test_evaluation_run_rejects_extra_fields() -> None:
    response = _client().post(
        "/evaluation-runs",
        json={"agent_version": "v1", "split": "development", "claims": []},
    )

    assert response.status_code == 422
    assert response.json() == {"type": "validation_error", "message": "invalid request"}


def test_get_run_not_found_is_404() -> None:
    response = _client().get(f"/evaluation-runs/{RUN_ID}")

    assert response.status_code == 404
    assert response.json() == {"type": "not_found", "message": "evaluation run not found"}


def test_get_run_rejects_invalid_uuid() -> None:
    response = _client().get("/evaluation-runs/not-a-uuid")

    assert response.status_code == 422
    assert response.json() == {"type": "validation_error", "message": "invalid request"}


def test_get_run_preserves_joined_fields_and_sorts_answers_and_claims() -> None:
    repo = FakeRepo()
    repo.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "answer_results": [
            {
                "answer_id": "A02",
                "claims": [
                    {"claim_id": "A02:9:12", "start_char": 9, "end_char": 12},
                    {"claim_id": "A02:1:4", "start_char": 1, "end_char": 4},
                ],
            },
            {"answer_id": "A01", "claims": []},
        ],
        "claim_results": [
            {"answer_id": "A02", "start_char": 9, "end_char": 12},
            {"answer_id": "A01", "start_char": 2, "end_char": 5},
        ],
    }
    client = _client(repo=repo)

    response = client.get(f"/evaluation-runs/{RUN_ID}")

    assert response.status_code == 200
    body = response.json()
    assert [row["answer_id"] for row in body["answer_results"]] == ["A01", "A02"]
    assert [row["claim_id"] for row in body["answer_results"][1]["claims"]] == [
        "A02:1:4",
        "A02:9:12",
    ]
    assert [row["answer_id"] for row in body["claim_results"]] == ["A01", "A02"]


def test_default_app_imports_without_optional_database_or_runner() -> None:
    from claimlens.api.app import app

    assert app.title == "ClaimLens"
