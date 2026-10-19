"""No-op tracing for local tests and a small Langfuse HTTP tracer for runs."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from .contracts import AgentInput, Tracer


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    return str(value)


class NoOpTracer(Tracer):
    async def start_answer(self, metadata: dict[str, Any], input_data: AgentInput) -> str | None:
        return None

    async def generation(self, trace_id: str | None, **kwargs: Any) -> None:
        return None

    async def tool_observation(self, trace_id: str | None, **kwargs: Any) -> None:
        return None

    async def end_answer(self, trace_id: str | None, **kwargs: Any) -> None:
        return None

    async def score(self, trace_id: str | None, **kwargs: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


class LangfuseTracer(Tracer):
    """Direct ingestion keeps this adapter stable across SDK major versions."""

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        self.public_key = public_key or os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.secret_key = secret_key or os.getenv("LANGFUSE_SECRET_KEY", "")
        self.host = (host or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")).rstrip("/")
        self.request_timeout = request_timeout
        self._events: list[dict[str, Any]] = []

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.secret_key and self.host)

    def _event(self, event_type: str, body: dict[str, Any]) -> None:
        self._events.append({"id": str(uuid.uuid4()), "type": event_type, "timestamp": _now(), "body": body})

    async def start_answer(self, metadata: dict[str, Any], input_data: AgentInput) -> str | None:
        if not self.configured:
            return None
        trace_id = str(uuid.uuid4())
        self._event(
            "trace-create",
            {
                "id": trace_id,
                "name": "claimlens.answer",
                "input": _jsonable(input_data),
                "metadata": _jsonable(metadata),
                "timestamp": _now(),
            },
        )
        return trace_id

    async def generation(self, trace_id: str | None, **kwargs: Any) -> None:
        if trace_id:
            metadata = kwargs.get("metadata", {})
            body = {
                "id": str(uuid.uuid4()),
                "traceId": trace_id,
                "name": kwargs.get("name", "model-turn"),
                "input": _jsonable(kwargs.get("input_data")),
                "output": _jsonable(kwargs.get("output_data")),
                "metadata": _jsonable(metadata),
                "startTime": _now(),
                "endTime": _now(),
            }
            self._event("generation-create", body)

    async def tool_observation(self, trace_id: str | None, **kwargs: Any) -> None:
        if trace_id:
            self._event(
                "span-create",
                {
                    "id": str(uuid.uuid4()),
                    "traceId": trace_id,
                    "name": kwargs.get("name", "get_brand_facts"),
                    "input": _jsonable(kwargs.get("input_data")),
                    "output": _jsonable(kwargs.get("output_data")),
                    "metadata": _jsonable(kwargs.get("metadata", {})),
                    "startTime": _now(),
                    "endTime": _now(),
                },
            )

    async def end_answer(self, trace_id: str | None, **kwargs: Any) -> None:
        if trace_id:
            self._event(
                "trace-create",
                {
                    "id": trace_id,
                    "output": _jsonable(kwargs.get("output_data")),
                    "metadata": _jsonable(kwargs.get("metadata", {})),
                    "updatedAt": _now(),
                },
            )

    async def score(self, trace_id: str | None, *, name: str, value: float, metadata: dict[str, Any]) -> None:
        if trace_id:
            self._event(
                "score-create",
                {"traceId": trace_id, "name": name, "value": value, "metadata": _jsonable(metadata)},
            )

    def _request(self, method: str, path: str, payload: Any = None) -> bytes:
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=body,
            method=method,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
            return response.read()

    async def flush(self) -> None:
        if not self.configured or not self._events:
            return
        events, self._events = self._events, []
        try:
            await asyncio.to_thread(self._request, "POST", "/api/public/ingestion", {"batch": events})
        except Exception:
            # Observability cannot make an experiment fail; the caller records the gap.
            self._events = events + self._events

    async def fetch_trace(self, trace_id: str) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Langfuse credentials are not configured")
        raw = await asyncio.to_thread(self._request, "GET", f"/api/public/traces/{trace_id}")
        return json.loads(raw)
