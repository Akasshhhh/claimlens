"""No-op tracing for local tests and a small Langfuse HTTP tracer for runs."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request
import uuid
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .contracts import AgentInput, Tracer

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


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
    """Small OTLP/HTTP adapter for Langfuse Cloud v4+.

    OTLP is used instead of the deprecated ingestion endpoint so observations
    are visible immediately through the v2 API used by the gate.
    """

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        self.public_key = public_key or os.getenv("LANGFUSE_PUBLIC_KEY", "")
        self.secret_key = secret_key or os.getenv("LANGFUSE_SECRET_KEY", "")
        self.host = (
            host
            or os.getenv("LANGFUSE_BASE_URL")
            or os.getenv("LANGFUSE_HOST")
            or "https://cloud.langfuse.com"
        ).rstrip("/")
        self.request_timeout = request_timeout
        self._traces: dict[str, dict[str, Any]] = {}
        self._score_events: list[dict[str, Any]] = []
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.secret_key and self.host)

    @staticmethod
    def _attribute(key: str, value: Any) -> dict[str, Any]:
        if isinstance(value, bool):
            wrapped = {"boolValue": value}
        elif isinstance(value, int) and not isinstance(value, bool):
            wrapped = {"intValue": value}
        elif isinstance(value, float):
            wrapped = {"doubleValue": value}
        else:
            wrapped = {"stringValue": str(value)}
        return {"key": key, "value": wrapped}

    @classmethod
    def _json_attribute(cls, key: str, value: Any) -> dict[str, Any]:
        return cls._attribute(key, json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True))

    @classmethod
    def _metadata_attributes(cls, prefix: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            cls._json_attribute(f"{prefix}.{key}", value)
            for key, value in metadata.items()
            if value is not None
        ]

    @staticmethod
    def _span(
        trace_id: str,
        span_id: str,
        name: str,
        *,
        parent_span_id: str | None = None,
        attributes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        now = str(time_ns())
        span = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": name,
            "kind": 1,
            "startTimeUnixNano": now,
            "endTimeUnixNano": now,
            "attributes": attributes,
            "status": {"code": 1},
        }
        if parent_span_id:
            span["parentSpanId"] = parent_span_id
        return span

    async def start_answer(self, metadata: dict[str, Any], input_data: AgentInput) -> str | None:
        if not self.configured:
            return None
        trace_id = uuid.uuid4().hex
        root_id = uuid.uuid4().hex[:16]
        attributes = [
            self._attribute("langfuse.observation.type", "span"),
            self._attribute("langfuse.trace.name", "claimlens.answer"),
            self._json_attribute("langfuse.observation.input", input_data),
        ]
        attributes.extend(self._metadata_attributes("langfuse.trace.metadata", metadata))
        root = self._span(trace_id, root_id, "claimlens.answer", attributes=attributes)
        self._traces[trace_id] = {"root": root, "root_id": root_id, "spans": [root]}
        return trace_id

    def _add_observation(
        self,
        trace_id: str | None,
        *,
        name: str,
        observation_type: str,
        input_data: Any,
        output_data: Any,
        metadata: dict[str, Any],
    ) -> None:
        if not trace_id or trace_id not in self._traces:
            return
        trace = self._traces[trace_id]
        attributes = [
            self._attribute("langfuse.observation.type", observation_type),
            self._json_attribute("langfuse.observation.input", input_data),
            self._json_attribute("langfuse.observation.output", output_data),
        ]
        attributes.extend(self._metadata_attributes("langfuse.observation.metadata", metadata))
        if observation_type == "generation":
            model = metadata.get("model")
            if model:
                attributes.append(self._attribute("langfuse.observation.model.name", model))
            attributes.append(self._attribute("gen_ai.system", "OpenAI"))
        span = self._span(
            trace_id,
            uuid.uuid4().hex[:16],
            name,
            parent_span_id=trace["root_id"],
            attributes=attributes,
        )
        trace["spans"].append(span)

    async def generation(self, trace_id: str | None, **kwargs: Any) -> None:
        self._add_observation(
            trace_id,
            name=kwargs.get("name", "model-turn"),
            observation_type="generation",
            input_data=kwargs.get("input_data"),
            output_data=kwargs.get("output_data"),
            metadata=kwargs.get("metadata", {}),
        )

    async def tool_observation(self, trace_id: str | None, **kwargs: Any) -> None:
        self._add_observation(
            trace_id,
            name=kwargs.get("name", "get_brand_facts"),
            observation_type="tool",
            input_data=kwargs.get("input_data"),
            output_data=kwargs.get("output_data"),
            metadata=kwargs.get("metadata", {}),
        )

    async def end_answer(self, trace_id: str | None, **kwargs: Any) -> None:
        if trace_id and trace_id in self._traces:
            root = self._traces[trace_id]["root"]
            root["endTimeUnixNano"] = str(time_ns())
            root["attributes"].append(
                self._json_attribute("langfuse.observation.output", kwargs.get("output_data"))
            )
            root["attributes"].extend(
                self._metadata_attributes("langfuse.observation.metadata", kwargs.get("metadata", {}))
            )

    async def score(self, trace_id: str | None, *, name: str, value: float, metadata: dict[str, Any]) -> None:
        if trace_id:
            self._score_events.append(
                {
                    "id": str(uuid.uuid4()),
                    "type": "score-create",
                    "timestamp": _now(),
                    "body": {
                        "id": str(uuid.uuid4()),
                        "traceId": trace_id,
                        "name": name,
                        "value": value,
                        "metadata": _jsonable(metadata),
                    },
                }
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        token = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
            return response.read()

    async def flush(self) -> None:
        if not self.configured:
            return
        traces, self._traces = self._traces, {}
        score_events, self._score_events = self._score_events, []
        if traces:
            payload = {
                "resourceSpans": [
                    {
                        "resource": {"attributes": [self._attribute("service.name", "claimlens")]},
                        "scopeSpans": [
                            {
                                "scope": {"name": "claimlens", "version": "0.1.0"},
                                "spans": [span for trace in traces.values() for span in trace["spans"]],
                            }
                        ],
                    }
                ]
            }
            try:
                await asyncio.to_thread(
                    self._request,
                    "POST",
                    "/api/public/otel/v1/traces",
                    payload,
                    {"x-langfuse-ingestion-version": "4"},
                )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        if score_events:
            try:
                await asyncio.to_thread(
                    self._request,
                    "POST",
                    "/api/public/ingestion",
                    {"batch": score_events},
                )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    async def fetch_trace(self, trace_id: str) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Langfuse credentials are not configured")
        for _ in range(10):
            now = datetime.now(timezone.utc)
            query = urllib.parse.urlencode(
                {
                    "traceId": trace_id,
                    "fields": "core,basic,io,metadata,model,usage,trace_context",
                    "limit": "100",
                    "fromStartTime": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    "toStartTime": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                }
            )
            raw = await asyncio.to_thread(
                self._request, "GET", f"/api/public/v2/observations?{query}"
            )
            payload = json.loads(raw)
            if payload.get("data"):
                return payload
            await asyncio.sleep(1)
        raise RuntimeError(f"Langfuse trace not visible through observations API: {trace_id}")


def time_ns() -> int:
    return time.time_ns()
