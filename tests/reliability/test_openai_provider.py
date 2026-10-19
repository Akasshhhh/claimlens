from __future__ import annotations

import json

import pytest

from claimlens.contracts import AgentInput
from providers.openai import OpenAIProvider
from runner.executor import AsyncExecutor


class FakeCompletions:
    def __init__(self, responses=None) -> None:
        self.kwargs = None
        self.responses = list(responses or [{"ok": True}])
        self.calls = 0

    async def create(self, **kwargs):
        self.kwargs = kwargs
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClient:
    def __init__(self, responses=None) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(responses)})()


class Http429Error(Exception):
    status_code = 429


class Http401Error(Exception):
    status_code = 401


@pytest.mark.asyncio
async def test_openai_request_uses_exact_model_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "o4-mini")
    client = FakeClient()
    provider = OpenAIProvider(client=client)

    result = await provider.complete([{"role": "user", "content": "hello"}], [])

    assert result == {"ok": True}
    assert client.chat.completions.kwargs == {
        "model": "o4-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
        "reasoning_effort": "minimal",
        "max_completion_tokens": 2000,
        "timeout": 15.0,
    }
    assert "temperature" not in client.chat.completions.kwargs
    assert provider.metadata["model"] == "o4-mini"
    assert provider.metadata["reasoning_effort"] == "minimal"
    assert provider.metadata["max_completion_tokens"] == 2000
    assert provider.metadata["temperature"] is None


@pytest.mark.asyncio
async def test_http_429_status_is_retried_end_to_end_through_agent(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"answer_id": "A01", "claims": []}),
                    "tool_calls": [],
                }
            }
        ]
    }
    client = FakeClient([Http429Error("rate limited"), response])
    provider = OpenAIProvider(client=client, model="o4-mini")

    class EmptyFactStore:
        brands = {"brand-a"}

        def get_brand_facts(self, brand_id):  # pragma: no cover - no tool call expected
            raise AssertionError("the final response should not need facts")

    outcome = (
        await AsyncExecutor(provider, EmptyFactStore(), retry_delay=0).run(
            [
                AgentInput(
                    answer_id="A01",
                    brand_id="brand-a",
                    question="What is stated?",
                    ai_answer="No factual claim.",
                )
            ]
        )
    )[0]

    assert outcome.status == "ok"
    assert outcome.claims == []
    assert client.chat.completions.calls == 2


@pytest.mark.asyncio
async def test_openai_auth_failure_is_not_retried(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = FakeClient([Http401Error("unauthorized")])
    provider = OpenAIProvider(client=client, model="o4-mini")

    class EmptyFactStore:
        brands = {"brand-a"}

    outcome = (
        await AsyncExecutor(provider, EmptyFactStore(), retry_delay=0).run(
            [
                AgentInput(
                    answer_id="A01",
                    brand_id="brand-a",
                    question="What is stated?",
                    ai_answer="No factual claim.",
                )
            ]
        )
    )[0]

    assert outcome.status == "error"
    assert outcome.error_type == "provider_failure"
    assert client.chat.completions.calls == 1


@pytest.mark.asyncio
async def test_malformed_openai_output_is_not_retried(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = FakeClient(
        [
            {
                "choices": [
                    {"message": {"content": "not-json", "tool_calls": []}},
                ]
            }
        ]
    )
    provider = OpenAIProvider(client=client, model="o4-mini")

    class EmptyFactStore:
        brands = {"brand-a"}

    outcome = (
        await AsyncExecutor(provider, EmptyFactStore(), retry_delay=0).run(
            [
                AgentInput(
                    answer_id="A01",
                    brand_id="brand-a",
                    question="What is stated?",
                    ai_answer="No factual claim.",
                )
            ]
        )
    )[0]

    assert outcome.status == "error"
    assert outcome.error_type == "invalid_output"
    assert client.chat.completions.calls == 1
