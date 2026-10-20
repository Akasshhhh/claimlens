from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claimlens.api import create_app
from claimlens.contracts import ProviderTransientError
from claimlens.db import PostgresRunRepository


def _database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.skip("DATABASE_URL is not configured")
    return value


@pytest.fixture()
def repository() -> PostgresRunRepository:
    try:
        value = PostgresRunRepository(_database_url())
        value.ensure_schema()
    except Exception as exc:  # pragma: no cover - depends on local services
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    return value


class _Agent:
    async def classify(self, input_data: Any, agent_version: str) -> dict[str, Any]:
        if input_data.brand_id == "secret-brand":
            raise ProviderTransientError("provider key should never be returned")
        return {"answer_id": input_data.answer_id, "claims": []}


class _Runner:
    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path

    async def run_split(
        self, agent_version: str, split: str, repository: PostgresRunRepository
    ) -> dict[str, Any]:
        run_id = repository.create_run(
            {
                "split": split,
                "agent_version": agent_version,
                "status": "running",
            }
        )
        claim = {
            "claim_id": "A01:0:4",
            "start_char": 0,
            "end_char": 4,
            "quote": "Fact",
            "label": "supported",
            "fact_ids": [],
            "reason": "catalogue",
        }
        return {
            "run_id": run_id,
            "status": "completed",
            "counts": {"scheduled": 3, "answers": 3, "ok": 2, "errors": 1, "predictions": 1},
            "metrics": {"joint_f1": 0.5} if split == "development" else {"joint_f1": None},
            "report_path": str(self.report_path),
            # Deliberately leave the outcomes unsorted.  The API and repository
            # must retain all rows and make retrieval deterministic.
            "scheduled_answer_ids": ["A03", "A01", "A02"],
            "answer_outcomes": [
                {
                    "answer_id": "A02",
                    "status": "ok",
                    "latency_ms": 2,
                    "claims": [],
                },
                {
                    "answer_id": "A03",
                    "status": "error",
                    "latency_ms": 3,
                    "error_type": "provider_failure",
                    "error_message": "provider secret must not be exposed",
                    "claims": [],
                },
                {"answer_id": "A01", "status": "ok", "latency_ms": 1, "claims": [claim]},
            ],
        }


def test_all_endpoints_use_real_postgres_and_post_get_agree(
    repository: PostgresRunRepository, tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "predictions": [
                    {
                        "answer_id": "A01",
                        "claim_id": "A01:0:4",
                        "start_char": 0,
                        "end_char": 4,
                        "quote": "Fact",
                        "label": "supported",
                        "fact_ids": [],
                        "reason": "catalogue",
                    }
                ],
                "matches": [
                    {
                        "answer_id": "A01",
                        "prediction_claim_id": "A01:0:4",
                        "gold_claim_id": "gold-A01",
                        "expected_label": "supported",
                        "label_correct": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(
        create_app(agent=_Agent(), runner=_Runner(report), repository=repository)
    )

    classify = client.post(
        "/classify",
        json={
            "answer_id": "C01",
            "brand_id": "known",
            "question": "Which facts?",
            "ai_answer": "No extracted claims.",
            "agent_version": "v1",
        },
    )
    assert classify.status_code == 200
    assert classify.json() == {"answer_id": "C01", "claims": []}

    invalid = client.post(
        "/classify",
        json={
            "answer_id": "C01",
            "brand_id": "known",
            "question": "Which facts?",
            "ai_answer": "No extracted claims.",
            "agent_version": "v1",
            "gold_claims": [],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"type": "validation_error", "message": "invalid request"}

    sanitized = client.post(
        "/classify",
        json={
            "answer_id": "C01",
            "brand_id": "secret-brand",
            "question": "Which facts?",
            "ai_answer": "No extracted claims.",
            "agent_version": "v1",
        },
    )
    assert sanitized.status_code == 502
    assert "provider key" not in sanitized.text

    created = client.post(
        "/evaluation-runs",
        json={"agent_version": "v1", "split": "development"},
    )
    assert created.status_code == 201
    posted = created.json()
    run_id = posted["run_id"]

    persisted = repository.get_run(run_id)
    assert persisted is not None
    assert posted["counts"] == persisted["counts"]
    assert posted["metrics"] == persisted["metrics"]
    assert [row["answer_id"] for row in persisted["answer_results"]] == [
        "A01",
        "A02",
        "A03",
    ]
    assert persisted["answer_results"][0]["claims"][0]["gold_claim_id"] == "gold-A01"
    assert persisted["answer_results"][0]["claims"][0]["label_correct"] is True
    assert persisted["answer_results"][1]["claims"] == []
    assert persisted["answer_results"][2]["status"] == "error"
    assert "provider secret" not in str(persisted["answer_results"][2])

    retrieved = client.get(f"/evaluation-runs/{run_id}")
    assert retrieved.status_code == 200
    body = retrieved.json()
    assert body["run_id"] == run_id
    assert [row["answer_id"] for row in body["answer_results"]] == ["A01", "A02", "A03"]
    assert [row["claim_id"] for row in body["claim_results"]] == ["A01:0:4"]
    assert body["counts"] == posted["counts"]
    assert body["metrics"] == posted["metrics"]

    assert client.get("/evaluation-runs/not-a-uuid").json() == {
        "type": "validation_error",
        "message": "invalid request",
    }
    assert client.get("/evaluation-runs/not-a-uuid").status_code == 422
