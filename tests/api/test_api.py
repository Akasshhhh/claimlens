from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from claimlens.api import create_app
from claimlens.contracts import AnswerOutcome, Claim, ProviderTimeoutError, UnknownBrandError
from claimlens.db import PostgresRunRepository, import_artifact


def _claim(answer_id: str = "A1") -> Claim:
    quote = "The model is red."
    return Claim(
        claim_id=f"{answer_id}:0:{len(quote)}",
        start_char=0,
        end_char=len(quote),
        quote=quote,
        label="supported",
        fact_ids=["fact-1"],
        reason="The catalogue states this.",
    )


class FakeAgent:
    async def classify(self, input_data: Any, agent_version: str) -> AnswerOutcome:
        if input_data.brand_id == "missing":
            raise UnknownBrandError("private provider text")
        if input_data.brand_id == "timeout":
            raise ProviderTimeoutError("private provider text")
        if input_data.brand_id == "invalid":
            return {"answer_id": input_data.answer_id, "claims": [{"bad": True}]}
        return AnswerOutcome(
            answer_id=input_data.answer_id,
            status="ok",
            latency_ms=1,
            claims=[_claim(input_data.answer_id)],
        )


class MemoryRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def create_run(self, meta: dict[str, Any]) -> str:
        run_id = str(meta.get("run_id") or uuid4())
        self.rows[run_id] = {
            "run_id": run_id,
            "status": meta.get("status", "running"),
            "split": meta.get("split", "development"),
            "counts": meta.get("counts", {}),
            "metrics": meta.get("metrics"),
            "answer_results": [],
        }
        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.rows.get(run_id)


class FakeRunner:
    async def run_split(self, agent_version: str, split: str, repo: MemoryRepository) -> dict[str, Any]:
        run_id = repo.create_run(
            {
                "split": split,
                "agent_version": agent_version,
                "status": "completed",
                "counts": {"scheduled": 2, "ok": 1, "errors": 1},
            }
        )
        return {
            "run_id": run_id,
            "status": "completed",
            "counts": {"scheduled": 2, "ok": 1, "errors": 1},
            "metrics": {"joint_f1": 0.5} if split == "development" else {"joint_f1": None},
        }


def test_classify_success_and_strict_input_validation() -> None:
    client = TestClient(create_app(agent=FakeAgent()))
    response = client.post(
        "/classify",
        json={
            "answer_id": "A1",
            "brand_id": "known",
            "question": "What colour?",
            "ai_answer": "The model is red.",
            "agent_version": "v1",
        },
    )
    assert response.status_code == 200
    assert response.json()["claims"][0]["claim_id"] == "A1:0:17"

    invalid = dict(response.request.content and json.loads(response.request.content))
    invalid["gold_claims"] = []
    assert client.post("/classify", json=invalid).status_code == 422


@pytest.mark.parametrize(
    ("brand_id", "status", "error_type"),
    [("missing", 404, "unknown_brand"), ("timeout", 504, "provider_timeout"), ("invalid", 502, "invalid_output")],
)
def test_classify_domain_errors_are_sanitized(brand_id: str, status: int, error_type: str) -> None:
    client = TestClient(create_app(agent=FakeAgent()))
    response = client.post(
        "/classify",
        json={
            "answer_id": "A1",
            "brand_id": brand_id,
            "question": "What?",
            "ai_answer": "The model is red.",
            "agent_version": "v2",
        },
    )
    assert response.status_code == status
    body = response.json()
    assert body["type"] == error_type
    assert "private provider text" not in response.text


def test_evaluation_run_and_uuid_lookup() -> None:
    repository = MemoryRepository()
    client = TestClient(create_app(runner=FakeRunner(), repository=repository))
    response = client.post("/evaluation-runs", json={"agent_version": "v1", "split": "development"})
    assert response.status_code == 201
    run_id = response.json()["run_id"]
    assert UUID(run_id)
    assert response.json()["metrics"]["joint_f1"] == 0.5
    assert client.get(f"/evaluation-runs/{run_id}").status_code == 200
    assert client.get("/evaluation-runs/not-a-uuid").status_code == 422
    assert client.get(f"/evaluation-runs/{uuid4()}").status_code == 404


def _database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not configured")
    return value


@pytest.fixture()
def postgres_repository() -> PostgresRunRepository:
    database_url = _database_url()
    try:
        repository = PostgresRunRepository(database_url)
        repository.ensure_schema()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    return repository


def test_postgres_persistence_and_stable_join(postgres_repository: PostgresRunRepository) -> None:
    repository = postgres_repository
    run_id = repository.create_run(
        {
            "split": "development",
            "agent_version": "v1",
            "prompt_version": "v1",
            "settings": {"request_timeout_seconds": 15},
            "file_hashes": {"answers.csv": "hash"},
        }
    )
    repository.save_answer_result(run_id, "B2", "ok", latency_ms=2, claims=[_claim("B2")])
    repository.save_answer_result(
        run_id,
        "A1",
        "error",
        error={"type": "provider_failure", "message": "safe"},
        latency_ms=3,
        claims=[],
    )
    repository.finish_run(run_id, "completed", {"scheduled": 2, "errors": 1}, {"joint_f1": None})
    result = repository.get_run(run_id)
    assert result is not None
    assert [row["answer_id"] for row in result["answer_results"]] == ["A1", "B2"]
    assert result["answer_results"][0]["status"] == "error"
    assert result["answer_results"][1]["claims"][0]["start_char"] == 0
    assert result["scores"]["joint_f1"] is None


def test_importer_preserves_empty_and_error_terminal_rows(
    postgres_repository: PostgresRunRepository, tmp_path: Path
) -> None:
    artifact = tmp_path / "run.json"
    artifact.write_text(
        json.dumps(
            {
                "split": "check",
                "agent_version": "v2",
                "answer_outcomes": [
                    {"answer_id": "A2", "status": "ok", "latency_ms": 1, "claims": []},
                    {
                        "answer_id": "A1",
                        "status": "error",
                        "latency_ms": 1,
                        "error_type": "provider_failure",
                        "error_message": "failed",
                        "claims": [],
                    },
                ],
                "scheduled_answer_ids": ["A1", "A2"],
                "metrics": {"joint_f1": None},
            }
        ),
        encoding="utf-8",
    )
    run_id = import_artifact(artifact, postgres_repository)
    result = postgres_repository.get_run(run_id)
    assert result is not None
    assert [row["answer_id"] for row in result["answer_results"]] == ["A1", "A2"]
    assert result["answer_results"][0]["status"] == "error"
    assert result["answer_results"][1]["claims"] == []
