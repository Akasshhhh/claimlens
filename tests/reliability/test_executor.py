from __future__ import annotations

import asyncio
import json

import pytest

from claimlens.contracts import (
    AgentInput,
    AnswerOutcome,
    Claim,
    ProviderAuthError,
    ProviderTransientError,
)
from runner.executor import AsyncExecutor, BoundedProvider, MAX_PROVIDER_CONCURRENCY


def _response(answer_id: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"answer_id": answer_id, "claims": []})
                }
            }
        ]
    }


def _input(answer_id: str) -> AgentInput:
    return AgentInput(
        answer_id=answer_id,
        brand_id="brand-a",
        question="Which speaker is this?",
        ai_answer="No factual claim.",
    )


class EmptyFactStore:
    brands = {"brand-a"}

    def get_brand_facts(self, brand_id: str):  # pragma: no cover - not called
        raise AssertionError("the empty-result tests should not call the tool")


class FakeProvider:
    def __init__(self, *, delay: float = 0.0, failures: dict[str, list[BaseException]] | None = None):
        self.delay = delay
        self.failures = failures or {}
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.overlap_observed = False
        self.retry_overlap_observed = False

    async def complete(self, messages, tools):
        answer_id = messages[1]["content"].splitlines()[0].split(": ", 1)[1]
        self.calls.append(answer_id)
        attempts = self.calls.count(answer_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= 2:
            self.overlap_observed = True
        if attempts > 1 and self.active >= 2:
            self.retry_overlap_observed = True
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            failures = self.failures.get(answer_id, [])
            if failures:
                raise failures.pop(0)
            return _response(answer_id)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_global_attempt_concurrency_is_three() -> None:
    provider = FakeProvider(delay=0.02)
    executor = AsyncExecutor(provider, EmptyFactStore())

    outcomes = await executor.run([_input(f"A{index:02d}") for index in range(8)])

    assert all(outcome.status == "ok" for outcome in outcomes)
    assert provider.overlap_observed is True
    assert provider.max_active == MAX_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_retry_attempts_share_global_concurrency_ceiling() -> None:
    provider = FakeProvider(
        delay=0.02,
        failures={"A01": [ProviderTransientError("temporary")]},
    )
    executor = AsyncExecutor(provider, EmptyFactStore(), retry_delay=0.001)

    outcomes = await executor.run([_input(f"A{index:02d}") for index in range(8)])

    assert all(outcome.status == "ok" for outcome in outcomes)
    assert provider.calls.count("A01") == 2
    assert provider.overlap_observed is True
    assert provider.retry_overlap_observed is True
    assert provider.max_active == MAX_PROVIDER_CONCURRENCY


@pytest.mark.asyncio
async def test_transient_failure_gets_one_retry_and_succeeds() -> None:
    provider = FakeProvider(
        failures={"A01": [ProviderTransientError("temporary")],}
    )
    executor = AsyncExecutor(provider, EmptyFactStore(), retry_delay=0.001)

    outcome = (await executor.run([_input("A01")]))[0]

    assert outcome.status == "ok"
    assert provider.calls == ["A01", "A01"]


@pytest.mark.asyncio
async def test_auth_failure_is_not_retried() -> None:
    provider = FakeProvider(failures={"A01": [ProviderAuthError("bad key")]})
    executor = AsyncExecutor(provider, EmptyFactStore(), retry_delay=0.001)

    outcome = (await executor.run([_input("A01")]))[0]

    assert outcome.status == "error"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_malformed_output_is_not_retried() -> None:
    class MalformedProvider(FakeProvider):
        async def complete(self, messages, tools):
            self.calls.append("A01")
            return {"choices": [{"message": {"content": "not-json"}}]}

    provider = MalformedProvider()
    executor = AsyncExecutor(provider, EmptyFactStore(), retry_delay=0.001)

    outcome = (await executor.run([_input("A01")]))[0]

    assert outcome.status == "error"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_failure_isolation_and_deterministic_answer_order() -> None:
    provider = FakeProvider(
        failures={
            "A02": [ProviderTransientError("temporary"), ProviderTransientError("still down")],
        }
    )
    executor = AsyncExecutor(provider, EmptyFactStore(), retry_delay=0.001)

    outcomes = await executor.run([_input("A10"), _input("A02"), _input("A01")])

    assert [outcome.answer_id for outcome in outcomes] == ["A01", "A02", "A10"]
    assert [outcome.status for outcome in outcomes] == ["ok", "error", "ok"]
    assert provider.calls.count("A02") == 2


@pytest.mark.asyncio
async def test_claim_spans_are_sorted_deterministically() -> None:
    class StaticAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, input_data):
            later = Claim(
                claim_id=f"{input_data.answer_id}:7:12",
                start_char=7,
                end_char=12,
                quote="claim",
                label="unknown",
                reason="later",
            )
            earlier = Claim(
                claim_id=f"{input_data.answer_id}:0:5",
                start_char=0,
                end_char=5,
                quote="No fa",
                label="unknown",
                reason="earlier",
            )
            return AnswerOutcome(
                answer_id=input_data.answer_id,
                status="ok",
                latency_ms=0,
                claims=[later, earlier],
            )

    outcomes = await AsyncExecutor(
        FakeProvider(),
        EmptyFactStore(),
        agent_factory=StaticAgent,
    ).run([_input("A01")])

    assert [claim.start_char for claim in outcomes[0].claims] == [0, 7]


@pytest.mark.asyncio
async def test_bounded_provider_maps_raw_timeout() -> None:
    class SlowProvider:
        async def complete(self, messages, tools):
            await asyncio.sleep(0.05)

    provider = BoundedProvider(SlowProvider(), request_timeout=0.001)

    with pytest.raises(TimeoutError):
        await provider.complete([], [])
