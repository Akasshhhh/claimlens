"""Thin FastAPI surface for classification and evaluation runs.

The routes intentionally contain no scoring or provider logic.  They validate
the HTTP boundary, call an injected agent/runner, and translate domain
failures into a small sanitized error envelope.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from claimlens.contracts import (
    AgentInput,
    AgentOutput,
    AnswerOutcome,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderTransientError,
    UnknownBrandError,
)


AgentVersion = Literal["v1", "v2"]
RunSplit = Literal["development", "check"]


class ClassifyRequest(BaseModel):
    """Strict request body for ``POST /classify``.

    Extra fields are rejected so gold rows, precomputed claims, and score
    fields cannot accidentally cross the inference boundary.
    """

    model_config = ConfigDict(extra="forbid")

    answer_id: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    question: str
    ai_answer: str
    agent_version: AgentVersion

    def input_data(self) -> AgentInput:
        return AgentInput(
            answer_id=self.answer_id,
            brand_id=self.brand_id,
            question=self.question,
            ai_answer=self.ai_answer,
        )


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_version: AgentVersion
    split: RunSplit


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str
    counts: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] | None = None


class _ApiError(Exception):
    def __init__(self, status_code: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message


class _InvalidAgentOutput(Exception):
    """Internal marker for output that fails the shared output contract."""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _jsonable(dump(mode="json"))
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return _jsonable(as_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _safe_error(error_type: str | None) -> tuple[int, str, str]:
    """Map domain failures to stable, non-sensitive public messages."""

    if error_type in {"unknown_brand", "unknown_primary_brand"}:
        return 404, "unknown_brand", "unknown primary brand"
    if error_type in {"provider_timeout", "timeout"}:
        return 504, "provider_timeout", "provider request timed out"
    if error_type in {"invalid_output", "illegal_tool", "turn_exhaustion"}:
        return 502, "invalid_output", "agent returned invalid output"
    return 502, "provider_failure", "agent request failed"


def _error_from_exception(exc: BaseException) -> _ApiError:
    if isinstance(exc, UnknownBrandError):
        status, error_type, message = _safe_error("unknown_brand")
    elif isinstance(exc, (ProviderTimeoutError, asyncio.TimeoutError, TimeoutError)):
        status, error_type, message = _safe_error("provider_timeout")
    elif isinstance(exc, (_InvalidAgentOutput, ValidationError)):
        status, error_type, message = _safe_error("invalid_output")
    elif isinstance(exc, (ProviderAuthError, ProviderTransientError)):
        status, error_type, message = _safe_error("provider_failure")
    else:
        status, error_type, message = _safe_error(None)
    return _ApiError(status, error_type, message)


def _error_from_outcome(outcome: AnswerOutcome) -> _ApiError:
    status, error_type, message = _safe_error(outcome.error_type)
    return _ApiError(status, error_type, message)


def _error_response(error: _ApiError) -> JSONResponse:
    # Keep the type/message shape useful to clients while never echoing a
    # provider exception, prompt text, credentials, or traceback.
    return JSONResponse(
        status_code=error.status_code,
        content={"type": error.error_type, "message": error.message},
    )


def _default_repository() -> Any | None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    try:
        module = importlib.import_module("claimlens.db.repository")
        repository_type = getattr(module, "PostgresRunRepository")
        return repository_type.from_url(database_url)
    except Exception:
        return None


def _default_agent(agent_version: AgentVersion, cache: dict[str, Any]) -> Any:
    if agent_version in cache:
        return cache[agent_version]
    try:
        from agent.core import ClaimAgent
        from claimlens.loaders import CsvFactStore
        from providers.openai import OpenAIProvider
        from runner.executor import AsyncExecutor

        data_dir = Path(os.getenv("CLAIMLENS_DATA_DIR", "."))
        provider = OpenAIProvider()
        store = CsvFactStore.from_csv(data_dir / "brand_facts.csv")
        # AsyncExecutor is the existing bounded interface; the API does not
        # duplicate its timeout, retry, or concurrency implementation.
        executor = AsyncExecutor(provider, store, agent_version=agent_version)
        cache[agent_version] = executor
        return executor
    except Exception as exc:
        raise _error_from_exception(exc) from exc


def _default_runner() -> Any | None:
    for module_name in ("claimlens.runner", "runner"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        if callable(getattr(module, "run_split", None)):
            return module
    return None


def _selected_agent(agent: Any, version: AgentVersion, cache: dict[str, Any]) -> Any:
    if agent is None:
        return _default_agent(version, cache)
    if isinstance(agent, Mapping):
        selected = agent.get(version)
        if selected is None:
            raise _ApiError(500, "configuration_error", "requested agent version is not configured")
        return selected
    if callable(agent) and not any(callable(getattr(agent, name, None)) for name in ("classify", "run", "run_one")):
        try:
            parameters = inspect.signature(agent).parameters
        except (TypeError, ValueError):
            parameters = {}
        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) == 1 and positional[0].name in {"version", "agent_version"}:
            return agent(version)
    return agent


def _method_args(method: Any, input_data: AgentInput, version: AgentVersion) -> tuple[tuple[Any, ...], dict[str, Any]]:
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return (input_data,), {}
    if "agent_version" in parameters:
        return (input_data,), {"agent_version": version}
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return (input_data,), {"agent_version": version}
    if len(positional) >= 2:
        return (input_data, version), {}
    return (input_data,), {}


async def _call_agent(agent: Any, input_data: AgentInput, version: AgentVersion) -> Any:
    method = next(
        (getattr(agent, name, None) for name in ("classify", "run_one", "run") if callable(getattr(agent, name, None))),
        None,
    )
    if method is None and callable(agent):
        method = agent
    if method is None:
        raise _ApiError(500, "configuration_error", "agent dependency is not configured")
    args, kwargs = _method_args(method, input_data, version)
    return await _await(method(*args, **kwargs))


def _outcome_from_agent_result(value: Any, input_data: AgentInput) -> AnswerOutcome:
    if isinstance(value, AnswerOutcome):
        if value.answer_id != input_data.answer_id:
            raise _InvalidAgentOutput("answer_id does not match request")
        outcome = value
        _validate_outcome_claims(outcome, input_data)
        return outcome
    if isinstance(value, AgentOutput):
        if value.answer_id != input_data.answer_id:
            raise _InvalidAgentOutput("answer_id does not match request")
        outcome = AnswerOutcome(
            answer_id=value.answer_id,
            status="ok",
            latency_ms=0,
            claims=value.claims,
        )
        _validate_outcome_claims(outcome, input_data)
        return outcome
    if isinstance(value, Mapping):
        raw = dict(value)
        if raw.get("status") == "error":
            try:
                outcome = AnswerOutcome.model_validate(raw)
            except ValidationError as exc:
                raise _InvalidAgentOutput(str(exc)) from exc
            return outcome
        try:
            output = AgentOutput.model_validate(raw)
        except ValidationError as exc:
            raise _InvalidAgentOutput(str(exc)) from exc
        if output.answer_id != input_data.answer_id:
            raise _InvalidAgentOutput("answer_id does not match request")
        outcome = AnswerOutcome(
            answer_id=output.answer_id,
            status="ok",
            latency_ms=0,
            claims=output.claims,
        )
        _validate_outcome_claims(outcome, input_data)
        return outcome
    raise _InvalidAgentOutput("agent result must be a validated output")


def _validate_outcome_claims(outcome: AnswerOutcome, input_data: AgentInput) -> None:
    """Re-check source spans at the HTTP boundary before accepting output."""

    if outcome.status != "ok":
        return
    seen: set[str] = set()
    previous_end = -1
    for claim in outcome.claims:
        if claim.claim_id in seen:
            raise _InvalidAgentOutput("claim IDs must be unique")
        if claim.start_char < previous_end:
            raise _InvalidAgentOutput("claims overlap or are not sorted")
        if claim.end_char > len(input_data.ai_answer) or claim.end_char <= claim.start_char:
            raise _InvalidAgentOutput("claim offsets are out of bounds")
        if claim.claim_id != f"{input_data.answer_id}:{claim.start_char}:{claim.end_char}":
            raise _InvalidAgentOutput("claim_id must derive from answer_id and offsets")
        if claim.quote != input_data.ai_answer[claim.start_char : claim.end_char] or claim.quote != claim.quote.strip():
            raise _InvalidAgentOutput("quote does not equal its exact source span")
        if len(claim.fact_ids) != len(set(claim.fact_ids)):
            raise _InvalidAgentOutput("fact_ids must be unique")
        seen.add(claim.claim_id)
        previous_end = claim.end_char


async def _classify(agent: Any, request: ClassifyRequest, *, cache: dict[str, Any], timeout: float | None) -> AnswerOutcome:
    input_data = request.input_data()
    selected = _selected_agent(agent, request.agent_version, cache)
    operation = _call_agent(selected, input_data, request.agent_version)
    try:
        value = await asyncio.wait_for(operation, timeout=timeout) if timeout else await operation
    except _ApiError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _error_from_exception(exc) from exc
    try:
        outcome = _outcome_from_agent_result(value, input_data)
    except _ApiError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _error_from_exception(exc) from exc
    if outcome.status == "error":
        raise _error_from_outcome(outcome)
    return outcome


def _counts_from_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("counts")
    if isinstance(raw, Mapping):
        return dict(raw)
    outcomes = payload.get("answer_outcomes", payload.get("outcomes", payload.get("answers", [])))
    if not isinstance(outcomes, list):
        outcomes = []
    return {
        "scheduled": len(outcomes),
        "answers": len(outcomes),
        "ok": sum(item.get("status") == "ok" for item in outcomes if isinstance(item, Mapping)),
        "errors": sum(item.get("status") == "error" for item in outcomes if isinstance(item, Mapping)),
        "predictions": sum(
            len(item.get("claims") or []) for item in outcomes if isinstance(item, Mapping)
        ),
    }


_QUALITY_METRICS = {
    "gold_claims",
    "matched_spans",
    "correct_labels",
    "extraction_precision",
    "extraction_recall",
    "extraction_f1",
    "joint_precision",
    "joint_recall",
    "joint_f1",
    "coverage",
    "per_label",
    "macro_f1",
    "confusion_matrix",
    "missed_ids",
    "unmatched_prediction_ids",
    "wrong_label_ids",
}


def _metrics_for_response(split: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if split == "check":
        # The check split has no labels available to this service.  Returning
        # one null envelope avoids accidentally presenting a partial quality
        # score as available.
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        return None
    metrics = dict(value)
    return metrics


def _run_envelope(value: Any, *, requested_split: RunSplit) -> dict[str, Any]:
    raw = _jsonable(value)
    if not isinstance(raw, Mapping):
        raw = {}
    metrics = _metrics_for_response(requested_split, raw.get("metrics", raw.get("scores")))
    result = {
        "run_id": str(raw.get("run_id", "")),
        "status": str(raw.get("status", "completed")),
        "counts": _counts_from_result(raw),
        "metrics": metrics,
    }
    if metrics is not None:
        result["available_metrics"] = [key for key, item in metrics.items() if item is not None]
    return result


def _safe_persisted_error(item: Mapping[str, Any]) -> dict[str, str]:
    """Keep answer-level errors useful without persisting provider details."""

    raw_type = item.get("error_type")
    raw_message = item.get("error_message")
    nested = item.get("error")
    if isinstance(nested, Mapping):
        raw_type = raw_type or nested.get("type", nested.get("error_type"))
        raw_message = raw_message or nested.get("message", nested.get("error_message"))
    if raw_type == "artifact_missing_outcome":
        return {
            "type": "artifact_missing_outcome",
            "message": "runner omitted terminal answer outcome",
        }
    _status, error_type, message = _safe_error(str(raw_type) if raw_type else None)
    return {"type": error_type, "message": message}


def _load_report_payload(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Read the runner's local report when it is available.

    The live runner writes development match metadata to ``report.json`` but
    returns terminal outcomes separately.  Reading that already-produced,
    local artifact lets the API persist the same match fields without making a
    second model call or importing the evaluator into the HTTP layer.
    """

    report_path = raw.get("report_path")
    if not isinstance(report_path, str) or not report_path:
        return None
    try:
        payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _enriched_outcomes(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Normalize runner outcomes and attach optional development matches."""

    report = _load_report_payload(raw)
    report = report or {}
    prediction_rows: list[Mapping[str, Any]] = []
    for source in (raw, report):
        candidates = source.get("predictions", source.get("accepted_predictions", []))
        if isinstance(candidates, list):
            prediction_rows.extend(item for item in candidates if isinstance(item, Mapping))
    matches_by_prediction: dict[str, dict[str, Any]] = {}
    for source in (raw, report):
        matches = source.get("matches", [])
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            prediction_id = match.get("prediction_claim_id", match.get("claim_id"))
            if isinstance(prediction_id, str):
                matches_by_prediction[prediction_id] = {
                    "gold_claim_id": match.get("gold_claim_id"),
                    "expected_label": match.get("expected_label"),
                    "label_correct": match.get("label_correct"),
                    "match": dict(match),
                }
    predictions_by_answer: dict[str, list[dict[str, Any]]] = {}
    for prediction in prediction_rows:
        answer_id = prediction.get("answer_id")
        claim_id = prediction.get("claim_id")
        if not isinstance(answer_id, str) or not isinstance(claim_id, str):
            continue
        claim = {key: value for key, value in prediction.items() if key != "answer_id"}
        claim.update(matches_by_prediction.get(claim_id, {}))
        predictions_by_answer.setdefault(answer_id, []).append(claim)

    candidates = raw.get("answer_outcomes", raw.get("outcomes", raw.get("answers", [])))
    outcomes = candidates if isinstance(candidates, list) else []
    normalized: list[Mapping[str, Any]] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        row = dict(outcome)
        answer_id = row.get("answer_id")
        claims = row.get("claims")
        if isinstance(answer_id, str):
            enriched_claims: list[dict[str, Any]] = []
            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, Mapping):
                        continue
                    enriched = dict(claim)
                    claim_id = enriched.get("claim_id")
                    if isinstance(claim_id, str):
                        enriched.update(matches_by_prediction.get(claim_id, {}))
                    enriched_claims.append(enriched)
            if not enriched_claims and answer_id in predictions_by_answer:
                enriched_claims = predictions_by_answer[answer_id]
            row["claims"] = enriched_claims
        normalized.append(row)
    return normalized


def _persist_runner_result(repository: Any, run_id: str, value: Any, envelope: Mapping[str, Any]) -> None:
    """Persist outcomes for a minimal runner that returned them to the API."""

    raw = _jsonable(value)
    if not isinstance(raw, Mapping):
        return
    outcomes = _enriched_outcomes(raw)
    by_answer: dict[str, Mapping[str, Any]] = {}
    for item in outcomes:
        answer_id = item.get("answer_id")
        if not isinstance(answer_id, str):
            continue
        if answer_id in by_answer:
            raise ValueError("runner returned duplicate answer outcomes")
        by_answer[answer_id] = item
    scheduled = raw.get("scheduled_answer_ids", list(by_answer))
    if not isinstance(scheduled, list):
        scheduled = list(by_answer)
    if any(not isinstance(answer_id, str) for answer_id in scheduled):
        raise ValueError("runner returned an invalid scheduled answer ID")
    if len(scheduled) != len(set(scheduled)):
        raise ValueError("runner returned duplicate scheduled answer IDs")
    extra = sorted(set(by_answer) - set(scheduled))
    if extra:
        raise ValueError("runner returned an unscheduled answer outcome")

    existing_by_answer: dict[str, Mapping[str, Any]] = {}
    getter = getattr(repository, "get_run", None)
    if callable(getter):
        try:
            existing = getter(run_id)
        except Exception:
            existing = None
        if isinstance(existing, Mapping):
            rows = existing.get("answer_results", existing.get("answers", []))
            if isinstance(rows, list):
                existing_by_answer = {
                    str(row["answer_id"]): row
                    for row in rows
                    if isinstance(row, Mapping) and isinstance(row.get("answer_id"), str)
                }
    save_outcome = getattr(repository, "save_outcome", None)
    save_answer = getattr(repository, "save_answer_result", None)
    for answer_id in sorted({item for item in scheduled if isinstance(item, str)}):
        item = dict(by_answer.get(answer_id) or {})
        item.setdefault("answer_id", answer_id)
        item.setdefault("status", "error")
        item.setdefault("latency_ms", 0)
        item.setdefault("claims", [])
        if item["status"] == "error":
            safe_error = (
                _safe_persisted_error({"error_type": "artifact_missing_outcome"})
                if not item.get("error_type") and not item.get("error_message") and not item.get("error")
                else _safe_persisted_error(item)
            )
            item["error_type"] = safe_error["type"]
            item["error_message"] = safe_error["message"]
            item["error"] = safe_error
        previous = existing_by_answer.get(answer_id)
        previous_claims = {
            str(claim["claim_id"]): claim
            for claim in (previous.get("claims", []) if isinstance(previous, Mapping) else [])
            if isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str)
        }
        claims: list[dict[str, Any]] = []
        for claim in item.get("claims") or []:
            if not isinstance(claim, Mapping):
                continue
            merged_claim = dict(previous_claims.get(str(claim.get("claim_id")), {}))
            merged_claim.update(claim)
            claims.append(merged_claim)
        item["claims"] = claims
        if callable(save_outcome):
            save_outcome(run_id, item)
        elif callable(save_answer):
            error = None
            if item.get("status") == "error":
                error = _safe_persisted_error(item)
            save_answer(
                run_id,
                answer_id,
                str(item["status"]),
                error,
                item.get("latency_ms"),
                item.get("trace_id", item.get("trace_ref")),
                list(item.get("claims") or []),
            )
    finish = getattr(repository, "finish_run", None)
    if callable(finish):
        finish(run_id, str(envelope["status"]), dict(envelope["counts"]), envelope.get("metrics"))


async def _run_split(runner: Any, version: AgentVersion, split: RunSplit, repository: Any) -> Any:
    method = getattr(runner, "run_split", None)
    if not callable(method) and callable(runner):
        method = runner
    if not callable(method):
        raise _ApiError(500, "configuration_error", "runner dependency is not configured")
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "repository" in parameters:
        args, kwargs = (version, split), {"repository": repository}
    elif "repo" in parameters:
        args, kwargs = (version, split), {"repo": repository}
    else:
        args, kwargs = (version, split, repository), {}
    keyword_only = {
        name
        for name, parameter in parameters.items()
        if parameter.kind == inspect.Parameter.KEYWORD_ONLY
    }
    if {"agent_version", "split"}.issubset(keyword_only):
        args = ()
        kwargs = {"agent_version": version, "split": split}
        if "repository" in keyword_only:
            kwargs["repository"] = repository
        elif "repo" in keyword_only:
            kwargs["repo"] = repository
    return await _await(method(*args, **kwargs))


def _repository_run(repository: Any, run_id: str) -> Any:
    getter = getattr(repository, "get_run", None)
    return getter(run_id) if callable(getter) else None


def create_app(
    *,
    agent: Any | None = None,
    runner: Any | None = None,
    repository: Any | None = None,
    repo: Any | None = None,
    classify_timeout: float | None = None,
) -> FastAPI:
    """Build an app with injectable agent, runner, and repository boundaries."""

    api = FastAPI(title="ClaimLens")
    api.state.agent = agent
    api.state.runner = runner
    api.state.repository = repository if repository is not None else repo
    api.state.classify_timeout = classify_timeout
    api.state.default_agent_cache = {}

    @api.exception_handler(_ApiError)
    async def api_error_handler(_request: Request, exc: _ApiError) -> JSONResponse:
        return _error_response(exc)

    @api.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's standard 422 shape is stable and includes field locations;
        # it is intentionally separate from provider error sanitization.
        return JSONResponse(status_code=422, content={"type": "validation_error", "message": "invalid request"})

    @api.post("/classify", status_code=200)
    async def classify(request: ClassifyRequest) -> dict[str, Any]:
        outcome = await _classify(
            api.state.agent,
            request,
            cache=api.state.default_agent_cache,
            timeout=api.state.classify_timeout,
        )
        return {
            "answer_id": outcome.answer_id,
            "claims": [_jsonable(claim) for claim in outcome.claims],
        }

    @api.post("/evaluation-runs", status_code=201)
    async def evaluation_runs(request: EvaluationRunRequest) -> JSONResponse:
        repository_value = api.state.repository or _default_repository()
        runner_value = api.state.runner or _default_runner()
        if repository_value is None:
            raise _ApiError(500, "configuration_error", "repository dependency is not configured")
        if runner_value is None:
            raise _ApiError(500, "configuration_error", "runner dependency is not configured")
        started = time.perf_counter()
        try:
            result = await _run_split(
                runner_value,
                request.agent_version,
                request.split,
                repository_value,
            )
        except _ApiError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Runner exceptions are infrastructure failures.  If the runner
            # attached a run ID to the exception, preserve its partial record.
            run_id = getattr(exc, "run_id", None)
            if run_id and callable(getattr(repository_value, "finish_run", None)):
                try:
                    repository_value.finish_run(
                        run_id,
                        "failed",
                        {"duration_ms": (time.perf_counter() - started) * 1000},
                        None,
                    )
                except Exception:
                    pass
            raise _ApiError(500, "run_failure", "evaluation run failed") from exc
        envelope = _run_envelope(result, requested_split=request.split)
        raw_result = _jsonable(result)
        created_here = False
        if not envelope["run_id"]:
            # A minimal runner may return only counts.  Persist a run here so
            # the API still has a retrievable UUID without duplicating a full
            # runner implementation.
            create = getattr(repository_value, "create_run", None)
            if not callable(create):
                raise _ApiError(500, "configuration_error", "repository cannot create runs")
            try:
                envelope["run_id"] = str(
                    create(
                        {
                            "run_id": None,
                            "split": request.split,
                            "agent_version": request.agent_version,
                            "status": envelope["status"],
                            "counts": envelope["counts"],
                            "metrics": envelope["metrics"],
                        }
                    )
                )
            except Exception as exc:
                raise _ApiError(500, "repository_failure", "could not create evaluation run") from exc
            created_here = True
        try:
            envelope["run_id"] = str(UUID(envelope["run_id"]))
        except (ValueError, AttributeError, TypeError) as exc:
            raise _ApiError(500, "run_failure", "evaluation run returned invalid run ID") from exc
        existing = None
        getter = getattr(repository_value, "get_run", None)
        if callable(getter) and not created_here:
            try:
                existing = getter(envelope["run_id"])
            except Exception:
                existing = None
        if existing is None and not created_here and callable(getattr(repository_value, "create_run", None)):
            try:
                repository_value.create_run(
                    {
                        "run_id": envelope["run_id"],
                        "split": request.split,
                        "agent_version": request.agent_version,
                        "status": envelope["status"],
                        "counts": envelope["counts"],
                        "metrics": envelope["metrics"],
                    }
                )
                created_here = True
            except Exception as exc:
                raise _ApiError(500, "repository_failure", "could not create evaluation run") from exc
        try:
            # Reconcile through the API even when the runner created the run.
            # This covers runners that reserve a run row first and return
            # outcomes afterward, while the repository's upserts keep a
            # runner that already persisted its rows idempotent.
            _persist_runner_result(repository_value, envelope["run_id"], raw_result, envelope)
        except Exception as exc:
            raise _ApiError(500, "repository_failure", "could not persist evaluation run") from exc
        return JSONResponse(status_code=201, content=envelope)

    @api.get("/evaluation-runs/{run_id}")
    async def get_evaluation_run(run_id: str) -> JSONResponse:
        try:
            parsed = UUID(run_id)
        except (ValueError, AttributeError):
            raise _ApiError(422, "validation_error", "invalid request")
        repository_value = api.state.repository or _default_repository()
        if repository_value is None:
            raise _ApiError(500, "configuration_error", "repository dependency is not configured")
        try:
            result = await _await(_repository_run(repository_value, str(parsed)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _ApiError(500, "repository_failure", "could not retrieve evaluation run") from exc
        if result is None:
            return JSONResponse(status_code=404, content={"type": "not_found", "message": "evaluation run not found"})
        result = _jsonable(result)
        if isinstance(result, Mapping):
            # A repository already orders these rows; sorting here also keeps
            # custom test repositories deterministic.
            for key in ("answer_results", "answers", "answer_outcomes"):
                rows = result.get(key)
                if isinstance(rows, list):
                    rows.sort(key=lambda row: str(row.get("answer_id", "")) if isinstance(row, Mapping) else str(row))
                    for row in rows:
                        if isinstance(row, Mapping) and isinstance(row.get("claims"), list):
                            row["claims"].sort(
                                key=lambda claim: (
                                    int(claim.get("start_char", 0)),
                                    int(claim.get("end_char", 0)),
                                    str(claim.get("claim_id", "")),
                                )
                            )
            rows = result.get("claim_results")
            if isinstance(rows, list):
                rows.sort(
                    key=lambda row: (
                        str(row.get("answer_id", "")) if isinstance(row, Mapping) else str(row),
                        int(row.get("start_char", 0)) if isinstance(row, Mapping) else 0,
                        int(row.get("end_char", 0)) if isinstance(row, Mapping) else 0,
                        str(row.get("claim_id", "")) if isinstance(row, Mapping) else str(row),
                    )
                )
        return JSONResponse(status_code=200, content=result)

    return api


app = create_app()


__all__ = [
    "ClassifyRequest",
    "EvaluationRunRequest",
    "EvaluationRunResponse",
    "app",
    "create_app",
]
