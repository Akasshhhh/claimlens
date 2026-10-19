"""Protocols for the thin HTTP layer.

The API depends on these small boundaries rather than importing the database
or experiment runner.  Concrete implementations are injected by the
application bootstrap and by tests.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Literal, Protocol

from claimlens.contracts import AgentInput, AgentOutput


class RunRepository(Protocol):
    def create_run(self, meta: dict) -> str:
        ...

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
        ...

    def finish_run(
        self,
        run_id: str,
        status: str,
        counts: dict,
        metrics: dict | None,
    ) -> None:
        ...

    def get_run(self, run_id: str) -> dict | None:
        ...


class Runner(Protocol):
    def run_split(
        self,
        agent_version: Literal["v1", "v2"],
        split: Literal["development", "check"],
        repo: RunRepository,
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]:
        ...


class Agent(Protocol):
    def classify(
        self,
        input_data: AgentInput,
        agent_version: Literal["v1", "v2"],
    ) -> AgentOutput | Awaitable[AgentOutput]:
        ...
