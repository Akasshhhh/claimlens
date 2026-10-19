"""No-op tracing for local tests and a small Langfuse HTTP tracer for runs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .contracts import AgentInput, Tracer

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_datetime(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def observation_window(
    started_at: datetime | str,
    completed_at: datetime | str,
    *,
    padding: timedelta = timedelta(minutes=1),
) -> tuple[str, str]:
    """Return a fixed UTC query window for one completed provider run."""

    if padding < timedelta(0):
        raise ValueError("padding must not be negative")
    started = _utc_datetime(started_at, field_name="started_at")
    completed = _utc_datetime(completed_at, field_name="completed_at")
    if completed < started:
        raise ValueError("completed_at must not precede started_at")
    return (
        (started - padding).isoformat().replace("+00:00", "Z"),
        (completed + padding).isoformat().replace("+00:00", "Z"),
    )


def observation_query(
    trace_id: str,
    *,
    from_start_time: datetime | str,
    to_start_time: datetime | str,
    limit: int = 10,
    fields: str = "core,basic",
) -> str:
    """Build the read-only observations path for one answer trace.

    The experiment UUID is deliberately not accepted as a separate argument:
    callers must pass the per-answer Langfuse trace ID and an explicit run
    window.
    """

    if not isinstance(trace_id, str) or not trace_id.strip():
        raise ValueError("trace_id must be a non-empty string")
    if limit <= 0:
        raise ValueError("limit must be positive")
    start = _utc_datetime(from_start_time, field_name="from_start_time")
    end = _utc_datetime(to_start_time, field_name="to_start_time")
    if end < start:
        raise ValueError("to_start_time must not precede from_start_time")
    query = urllib.parse.urlencode(
        {
            "traceId": trace_id,
            "fromStartTime": start.isoformat().replace("+00:00", "Z"),
            "toStartTime": end.isoformat().replace("+00:00", "Z"),
            "limit": str(limit),
            "fields": fields,
        }
    )
    return f"/api/public/v2/observations?{query}"


def _safe_error_text(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text[:300]


def _response_summary(status: int | None, raw: bytes) -> dict[str, Any]:
    """Summarize a response without retaining model data or response bodies."""

    summary: dict[str, Any] = {
        "status": status,
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "observation_count": None,
        "observation_names": [],
    }
    try:
        payload = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return summary
    observations = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(observations, list):
        summary["observation_count"] = len(observations)
        summary["observation_names"] = [
            item.get("name")
            for item in observations
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ][:50]
    return summary


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

    async def wait_for_flush(self) -> None:
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
        request_timeout: float = 45.0,
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
        self._pending_flushes: set[asyncio.Task[Any]] = set()
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

    def _request_with_status(
        self,
        method: str,
        path: str,
        payload: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Return HTTP status and body so verification can classify responses."""

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
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    async def _send_pending(
        self,
        traces: dict[str, dict[str, Any]],
        score_events: list[dict[str, Any]],
    ) -> None:
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

    async def flush(self) -> None:
        """Schedule ingestion and return without blocking the model run."""

        if not self.configured:
            return
        traces, self._traces = self._traces, {}
        score_events, self._score_events = self._score_events, []
        if not traces and not score_events:
            return
        task = asyncio.create_task(self._send_pending(traces, score_events))
        self._pending_flushes.add(task)
        task.add_done_callback(self._pending_flushes.discard)

    async def wait_for_flush(self) -> None:
        """Wait only at run teardown, before trace verification."""

        while self._pending_flushes:
            pending = tuple(self._pending_flushes)
            await asyncio.gather(*pending, return_exceptions=True)

    async def verify_trace(
        self,
        trace_id: str,
        *,
        max_wait_seconds: float = 1200.0,
        poll_seconds: float = 5.0,
        from_start_time: datetime | str | None = None,
        to_start_time: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Poll observations by trace ID over the trace's actual run window."""

        if not self.configured:
            raise RuntimeError("Langfuse credentials are not configured")
        deadline = time.monotonic() + max_wait_seconds
        now = datetime.now(timezone.utc)

        def _iso(value: datetime | str | None, default: datetime) -> str:
            if value is None:
                parsed = default
            elif isinstance(value, datetime):
                parsed = value
            else:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        path = observation_query(
            trace_id,
            from_start_time=_iso(from_start_time, now - timedelta(minutes=5)),
            to_start_time=_iso(to_start_time, now + timedelta(minutes=5)),
        )
        last_response: dict[str, Any] = {"status": None, "body_sha256": None}
        while True:
            try:
                status, raw = await asyncio.to_thread(self._request_with_status, "GET", path)
                last_response = _response_summary(status, raw)
            except (TimeoutError, urllib.error.URLError) as exc:
                if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, TimeoutError):
                    raise RuntimeError(
                        f"Langfuse trace fetch failed: {_safe_error_text(exc)}"
                    ) from exc
                status, raw = None, b""
                last_response = {
                    "status": None,
                    "error_type": type(exc).__name__,
                    "error_message": _safe_error_text(exc),
                }
            else:
                if 200 <= status < 300:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            "Langfuse trace fetch returned invalid JSON: "
                            f"{json.dumps(last_response, sort_keys=True)}"
                        ) from exc
                    observations = payload.get("data")
                    if isinstance(observations, list) and observations:
                        return payload
                elif 500 <= status < 600:
                    pass
                elif 400 <= status < 500:
                    raise RuntimeError(
                        "Langfuse trace fetch failed: "
                        f"{json.dumps(last_response, sort_keys=True)}"
                    )
                else:
                    raise RuntimeError(
                        "Langfuse trace fetch failed: "
                        f"{json.dumps(last_response, sort_keys=True)}"
                    )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Langfuse trace not ingested after {max_wait_seconds:.0f}s: "
                    f"{json.dumps(last_response, sort_keys=True)}"
                )
            await asyncio.sleep(min(poll_seconds, remaining))

    async def fetch_trace(self, trace_id: str) -> dict[str, Any]:
        """Backward-compatible name for the strict polling verifier."""

        return await self.verify_trace(trace_id)


def time_ns() -> int:
    return time.time_ns()


__all__ = ["LangfuseTracer", "NoOpTracer", "observation_query", "observation_window"]
