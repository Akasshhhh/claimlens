from __future__ import annotations

import pytest

from providers.openai import OpenAIProvider


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return {"ok": True}


class FakeClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions()})()


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
        "max_completion_tokens": 3000,
        "timeout": 15.0,
    }
    assert "temperature" not in client.chat.completions.kwargs
    assert provider.metadata["model"] == "o4-mini"
    assert provider.metadata["reasoning_effort"] == "minimal"
    assert provider.metadata["max_completion_tokens"] == 3000
    assert provider.metadata["temperature"] is None
