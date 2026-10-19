"""Failure-isolated, bounded execution for independent answers."""

from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import Sequence
from threading import Lock
from typing import Any, Callable

from agent.core import ClaimAgent
from claimlens.contracts import (
    AgentInput,
    AnswerOutcome,
    FactStore,
    Provider,
    ProviderAuthError,
    ProviderTimeoutError,
)


MAX_PROVIDER_CONCURRENCY = 3
DEFAULT_REQUEST_TIMEOUT = 15.0
DEFAULT_RETRY_DELAY = 0.05
MAX_RETRY_DELAY = 1.0


class _GlobalSemaphore:
    """One logical process-wide limiter, safe across sequential test loops."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._semaphores: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )
        self._lock = Lock()

    def _current(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._lock:
            semaphore = self._semaphores.get(loop)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self.limit)
                self._semaphores[loop] = semaphore
            return semaphore

    async def acquire(self) -> None:
        await self._current().acquire()

    def release(self) -> None:
        self._current().release()

    async def __aenter__(self) -> "_GlobalSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.release()


# Keep one limiter object for the whole process.  Separate event-loop backing
# semaphores only avoid asyncio's cross-loop restriction in local test runners.
GLOBAL_PROVIDER_SEMAPHORE = _GlobalSemaphore(MAX_PROVIDER_CONCURRENCY)


def _metadata(value: Any) -> dict[str, Any]:
    raw = getattr(value, "metadata", {})
    raw = raw() if callable(raw) else raw
    return dict(raw) if isinstance(raw, dict) else {}


class BoundedProvider:
    """Wrap a provider so each individual attempt holds the global permit."""

    def __init__(
        self,
        provider: Provider,
        *,
        semaphore: _GlobalSemaphore = GLOBAL_PROVIDER_SEMAPHORE,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.provider = provider
        self.semaphore = semaphore
        self.request_timeout = float(request_timeout)

    @property
    def metadata(self) -> dict[str, Any]:
        result = _metadata(self.provider)
        result.setdefault("provider", type(self.provider).__name__)
        result.setdefault("model", getattr(self.provider, "model", None))
        result["request_timeout_seconds"] = self.request_timeout
        result["max_provider_concurrency"] = MAX_PROVIDER_CONCURRENCY
        return result

    @property
    def settings(self) -> dict[str, Any]:
        return self.metadata

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        # The permit is acquired for this one attempt only.  ClaimAgent calls
        # this method again for a retry, so retries cannot bypass the limit.
        async with self.semaphore:
            try:
                return await asyncio.wait_for(
                    self.provider.complete(messages, tools),
                    timeout=self.request_timeout,
                )
            except ProviderTimeoutError:
                raise
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise ProviderTimeoutError("provider request timed out") from exc


def _bounded(provider: Provider, request_timeout: float) -> BoundedProvider:
    if isinstance(provider, BoundedProvider):
        return provider
    return BoundedProvider(provider, request_timeout=request_timeout)


def _ordered(outcome: AnswerOutcome) -> AnswerOutcome:
    claims = sorted(
        outcome.claims,
        key=lambda claim: (claim.start_char, claim.end_char, claim.claim_id),
    )
    if claims == outcome.claims:
        return outcome
    return outcome.model_copy(update={"claims": claims})


def _failure(
    input_data: AgentInput,
    started: float,
    exc: BaseException,
) -> AnswerOutcome:
    if isinstance(exc, (ProviderTimeoutError, asyncio.TimeoutError, TimeoutError)):
        error_type = "provider_timeout"
    elif isinstance(exc, ProviderAuthError):
        error_type = "provider_failure"
    else:
        error_type = "executor_failure"
    message = str(exc).replace("\n", " ").replace("\r", " ")[:500]
    return AnswerOutcome(
        answer_id=input_data.answer_id,
        status="error",
        latency_ms=(time.perf_counter() - started) * 1000,
        error_type=error_type,
        error_message=message or type(exc).__name__,
        claims=[],
    )


class AsyncExecutor:
    """Run answer agents concurrently while isolating terminal outcomes."""

    def __init__(
        self,
        provider: Provider,
        fact_store: FactStore,
        tracer: Any | None = None,
        *,
        agent_version: str = "v1",
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
        max_turns: int = 3,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        agent_factory: Callable[..., Any] = ClaimAgent,
    ) -> None:
        self.request_timeout = float(request_timeout)
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.retry_delay = min(max(float(retry_delay), 0.0), MAX_RETRY_DELAY)
        self.provider = _bounded(provider, self.request_timeout)
        self.fact_store = fact_store
        self.tracer = tracer
        self.agent_version = agent_version
        self.max_turns = max_turns
        self.agent = agent_factory(
            self.provider,
            fact_store,
            tracer,
            agent_version=agent_version,
            request_timeout=self.request_timeout,
            max_turns=max_turns,
            retry_delay=self.retry_delay,
        )

    @property
    def metadata(self) -> dict[str, Any]:
        result = self.provider.metadata
        result.update(
            {
                "agent_version": self.agent_version,
                "max_turns": self.max_turns,
                "max_attempts_per_turn": 2,
                "retry_delay_seconds": self.retry_delay,
                "max_provider_concurrency": MAX_PROVIDER_CONCURRENCY,
            }
        )
        return result

    async def run_one(self, input_data: AgentInput) -> AnswerOutcome:
        started = time.perf_counter()
        try:
            outcome = await self.agent.run(input_data)
            if not isinstance(outcome, AnswerOutcome):
                raise TypeError("agent did not return AnswerOutcome")
            return _ordered(outcome)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _failure(input_data, started, exc)

    async def run(self, inputs: Sequence[AgentInput]) -> list[AnswerOutcome]:
        tasks = [asyncio.create_task(self.run_one(input_data)) for input_data in inputs]
        outcomes = await asyncio.gather(*tasks)
        return sorted(outcomes, key=lambda outcome: outcome.answer_id)

    async def execute(self, inputs: Sequence[AgentInput]) -> list[AnswerOutcome]:
        return await self.run(inputs)

    async def run_many(self, inputs: Sequence[AgentInput]) -> list[AnswerOutcome]:
        return await self.run(inputs)


Executor = AsyncExecutor


async def run_batch(
    inputs: Sequence[AgentInput],
    provider: Provider,
    fact_store: FactStore,
    tracer: Any | None = None,
    **kwargs: Any,
) -> list[AnswerOutcome]:
    return await AsyncExecutor(provider, fact_store, tracer, **kwargs).run(inputs)


async def execute_batch(
    inputs: Sequence[AgentInput],
    provider: Provider,
    fact_store: FactStore,
    tracer: Any | None = None,
    **kwargs: Any,
) -> list[AnswerOutcome]:
    return await run_batch(inputs, provider, fact_store, tracer, **kwargs)


__all__ = [
    "AsyncExecutor",
    "BoundedProvider",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_RETRY_DELAY",
    "Executor",
    "GLOBAL_PROVIDER_SEMAPHORE",
    "MAX_PROVIDER_CONCURRENCY",
    "MAX_RETRY_DELAY",
    "execute_batch",
    "run_batch",
]
