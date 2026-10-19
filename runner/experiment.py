"""Live split runner, immutable artifacts, and Langfuse run verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.core import ClaimAgent
from claimlens.contracts import (
    DATASET_VERSION,
    EVALUATOR_VERSION,
    FACT_VERSION,
    AgentInput,
    AnswerOutcome,
    Tracer,
)
from claimlens.loaders import csv_hashes, load_answers, load_facts, load_gold, sha256_file
from claimlens.tracing import LangfuseTracer, NoOpTracer
from harness.evaluator import EvaluationResult, evaluate_split
from harness.reports import write_report
from providers.openai import OpenAIProvider

from .executor import AsyncExecutor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_revision(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return None


def _summary(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        summary: dict[str, Any] = {"keys": sorted(str(key) for key in value)}
        if "choices" in value:
            summary["choices"] = len(value["choices"]) if isinstance(value["choices"], list) else 1
        if "tool_calls" in value:
            summary["tool_calls"] = len(value["tool_calls"] or [])
        return summary
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _summary(dump(mode="python"))
    return {"type": type(value).__name__}


class RecordingTracer(Tracer):
    """Record sanitized local evidence while delegating real tracing."""

    def __init__(self, base: Tracer) -> None:
        self.base = base
        self.events: list[dict[str, Any]] = []

    async def start_answer(self, metadata: dict[str, Any], input_data: AgentInput) -> str | None:
        trace_id = await self.base.start_answer(metadata, input_data)
        self.events.append(
            {
                "kind": "trace_start",
                "answer_id": input_data.answer_id,
                "trace_id": trace_id,
                "metadata": {key: value for key, value in metadata.items() if key != "answer_text"},
            }
        )
        return trace_id

    async def generation(self, trace_id: str | None, **kwargs: Any) -> None:
        await self.base.generation(trace_id, **kwargs)
        self.events.append(
            {
                "kind": "generation",
                "trace_id": trace_id,
                "name": kwargs.get("name"),
                "metadata": kwargs.get("metadata", {}),
                "output_summary": _summary(kwargs.get("output_data")),
            }
        )

    async def tool_observation(self, trace_id: str | None, **kwargs: Any) -> None:
        await self.base.tool_observation(trace_id, **kwargs)
        input_data = kwargs.get("input_data") or {}
        output_data = kwargs.get("output_data") or {}
        facts = output_data.get("facts", []) if isinstance(output_data, Mapping) else []
        fact_ids = [fact.get("fact_id") for fact in facts if isinstance(fact, Mapping)]
        self.events.append(
            {
                "kind": "tool_observation",
                "trace_id": trace_id,
                "name": kwargs.get("name", "get_brand_facts"),
                "brand_id": input_data.get("arguments", {}).get("brand_id")
                if isinstance(input_data, Mapping)
                and isinstance(input_data.get("arguments"), Mapping)
                else None,
                "fact_count": len(fact_ids),
                "fact_ids": fact_ids,
            }
        )

    async def end_answer(self, trace_id: str | None, **kwargs: Any) -> None:
        await self.base.end_answer(trace_id, **kwargs)
        output = kwargs.get("output_data")
        self.events.append(
            {
                "kind": "answer_end",
                "trace_id": trace_id,
                "status": getattr(output, "status", None),
                "error_type": getattr(output, "error_type", None),
                "claim_ids": [claim.claim_id for claim in getattr(output, "claims", [])],
            }
        )

    async def provider_attempt(self, trace_id: str | None, **kwargs: Any) -> None:
        """Store request lifecycle diagnostics without sending raw prompts."""

        self.events.append(
            {
                "kind": "provider_attempt",
                "trace_id": trace_id,
                "phase": kwargs.get("phase"),
                "turn": kwargs.get("turn"),
                "attempt": kwargs.get("attempt"),
                "error_type": kwargs.get("error_type"),
                "error_message": str(kwargs.get("error_message", ""))[:500]
                if kwargs.get("error_message")
                else None,
            }
        )

    async def answer_diagnostics(self, trace_id: str | None, **kwargs: Any) -> None:
        self.events.append(
            {
                "kind": "answer_diagnostics",
                "trace_id": trace_id,
                "attempts_used": kwargs.get("attempts_used"),
                "turns_used": kwargs.get("turns_used"),
                "attempted_turns": kwargs.get("attempted_turns"),
                "error_type": kwargs.get("error_type"),
                "error_message": str(kwargs.get("error_message", ""))[:500]
                if kwargs.get("error_message")
                else None,
            }
        )

    async def score(self, trace_id: str | None, *, name: str, value: float, metadata: dict[str, Any]) -> None:
        await self.base.score(trace_id, name=name, value=value, metadata=metadata)
        self.events.append({"kind": "score", "trace_id": trace_id, "name": name, "value": value, "metadata": metadata})

    async def flush(self) -> None:
        await self.base.flush()

    async def wait_for_flush(self) -> None:
        waiter = getattr(self.base, "wait_for_flush", None)
        if callable(waiter):
            await waiter()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _outcome_dict(outcome: AnswerOutcome) -> dict[str, Any]:
    return outcome.model_dump(mode="json")


async def _verify_trace(trace: LangfuseTracer, trace_id: str) -> dict[str, Any]:
    started = time.monotonic()
    payload = await trace.verify_trace(trace_id)
    observations = payload.get("data", []) if isinstance(payload, Mapping) else []
    return {
        "trace_id": trace_id,
        "observation_count": len(observations) if isinstance(observations, list) else 0,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "observation_names": [
            item.get("name") for item in observations if isinstance(item, Mapping) and item.get("name")
        ],
    }


async def _verify_traces(
    trace: LangfuseTracer,
    trace_ids: Sequence[str],
    *,
    spacing_seconds: float = 2.1,
) -> list[dict[str, Any]]:
    """Verify traces sequentially to stay below Langfuse's read rate limit."""

    verified: list[dict[str, Any]] = []
    for index, trace_id in enumerate(trace_ids):
        if index:
            await asyncio.sleep(spacing_seconds)
        verified.append(await _verify_trace(trace, trace_id))
    return verified


async def run_split(
    agent_version: str,
    split: str,
    repository: Any | None = None,
    *,
    data_dir: str | Path | None = None,
    artifact_dir: str | Path | None = None,
    artifact_label: str | None = None,
    answer_ids: Sequence[str] | None = None,
    tracer: Tracer | None = None,
    provider: Any | None = None,
    verify_traces: bool = True,
    supersedes: str | None = None,
    supersedes_reason: str | None = None,
) -> dict[str, Any]:
    """Run one fixed split and save all evidence before returning."""

    root = Path(__file__).resolve().parent.parent
    data_root = Path(data_dir or root)
    output_root = Path(artifact_dir or (root / "artifacts"))
    output_root.mkdir(parents=True, exist_ok=True)
    answers = load_answers(data_root / "answers.csv", split=split)
    if answer_ids is not None:
        wanted = set(answer_ids)
        answers = [answer for answer in answers if answer.answer_id in wanted]
        missing = wanted - {answer.answer_id for answer in answers}
        if missing:
            raise ValueError(f"answer IDs are not in split {split}: {sorted(missing)}")
    answers = sorted(answers, key=lambda answer: answer.answer_id)
    if not answers:
        raise ValueError(f"split has no scheduled answers: {split}")

    if provider is None:
        provider = OpenAIProvider()
    base_tracer = tracer or LangfuseTracer()
    if verify_traces and isinstance(base_tracer, LangfuseTracer) and not base_tracer.configured:
        raise RuntimeError("Langfuse credentials are required for a verified live run")
    recorder = RecordingTracer(base_tracer)
    run_id = str(uuid.uuid4())
    prompt_path = root / "prompts" / f"{agent_version}.txt"
    prompt_hash = sha256_file(prompt_path)
    hashes = csv_hashes(data_root)
    hashes[f"prompts/{agent_version}.txt"] = prompt_hash
    settings = dict(getattr(provider, "settings", {}) or {})
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "split": split,
        "agent_version": agent_version,
        "prompt_version": agent_version,
        "dataset_version": DATASET_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "fact_version": FACT_VERSION,
        "provider": settings.get("provider", "openai"),
        "model": settings.get("model"),
        "settings": settings,
        "source_revision": _git_revision(root),
        "file_hashes": hashes,
        "started_at": _now(),
    }
    if supersedes:
        metadata["supersedes"] = supersedes
        metadata["supersedes_reason"] = supersedes_reason or "infrastructure rerun"

    if repository is not None:
        repository.create_run({**metadata, "status": "running"})

    fact_store = __import__("claimlens.loaders", fromlist=["CsvFactStore"]).CsvFactStore.from_csv(
        data_root / "brand_facts.csv"
    )
    executor = AsyncExecutor(
        provider,
        fact_store,
        recorder,
        agent_version=agent_version,
        run_metadata=metadata,
    )
    started = time.monotonic()
    outcomes = await executor.run(
        [
            AgentInput(
                answer_id=answer.answer_id,
                brand_id=answer.brand_id,
                question=answer.question,
                ai_answer=answer.ai_answer,
            )
            for answer in answers
        ]
    )
    duration_ms = (time.monotonic() - started) * 1000
    gold = None
    if split == "development":
        scheduled_ids = {answer.answer_id for answer in answers}
        gold = [
            claim
            for claim in load_gold(data_root / "development_gold.csv")
            if claim.answer_id in scheduled_ids
        ]
    result: EvaluationResult = evaluate_split(
        split,
        answers,
        outcomes,
        gold,
        labeled=split == "development",
    )

    trace_by_answer = {outcome.answer_id: outcome.trace_id for outcome in outcomes if outcome.trace_id}
    if split == "development":
        for score in result.answer_scores:
            trace_id = trace_by_answer.get(score.answer_id)
            if trace_id:
                await recorder.score(trace_id, name="extraction_f1", value=score.extraction_f1, metadata={"run_id": run_id, "answer_id": score.answer_id})
                await recorder.score(trace_id, name="joint_f1", value=score.joint_f1, metadata={"run_id": run_id, "answer_id": score.answer_id})
        for match in result.matches:
            trace_id = trace_by_answer.get(match.answer_id)
            if trace_id:
                await recorder.score(
                    trace_id,
                    name="label_correct",
                    value=1.0 if match.label_correct else 0.0,
                    metadata={"run_id": run_id, "span_id": match.gold_claim_id},
                )
        if outcomes:
            first_trace = outcomes[0].trace_id
            if first_trace:
                await recorder.score(first_trace, name="aggregate_extraction_f1", value=result.metrics.extraction_f1 or 0.0, metadata={"run_id": run_id})
                await recorder.score(first_trace, name="aggregate_joint_f1", value=result.metrics.joint_f1 or 0.0, metadata={"run_id": run_id})

    label = artifact_label or f"{agent_version}-{split}-{run_id}"
    directory = output_root / label
    directory.mkdir(parents=True, exist_ok=True)
    trace_ids = sorted({outcome.trace_id for outcome in outcomes if outcome.trace_id})
    metadata.update(
        {
            "completed_at": _now(),
            "duration_ms": duration_ms,
            "scheduled_answer_ids": [answer.answer_id for answer in answers],
            "trace_ids": trace_ids,
            "counts": {
                "scheduled": len(answers),
                "answers": len(outcomes),
                "ok": sum(outcome.status == "ok" for outcome in outcomes),
                "errors": sum(outcome.status == "error" for outcome in outcomes),
                "predictions": len(result.accepted_predictions),
            },
        }
    )
    report_path = directory / "report.json"
    write_report(report_path, result, metadata=metadata)
    _write_json(directory / "run.json", metadata)
    _write_json(directory / "outcomes.json", [_outcome_dict(outcome) for outcome in outcomes])
    _write_json(directory / "predictions.json", list(result.accepted_predictions))
    _write_json(directory / "timings.json", {"duration_ms": duration_ms, "answer_latencies_ms": {outcome.answer_id: outcome.latency_ms for outcome in outcomes}})
    _write_json(directory / "config.json", {"metadata": metadata, "provider_settings": settings})
    (directory / "exchanges.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in recorder.events),
        encoding="utf-8",
    )

    if repository is not None:
        for outcome in outcomes:
            repository.save_outcome(run_id, outcome)

    await recorder.flush()
    await recorder.wait_for_flush()
    verification: list[dict[str, Any]] = []
    try:
        if verify_traces and isinstance(base_tracer, LangfuseTracer):
            verification = await _verify_traces(base_tracer, trace_ids)
            if len(verification) != len(trace_ids):
                raise RuntimeError("Langfuse trace verification returned incomplete results")
    except Exception as exc:
        _write_json(directory / "trace_verification_error.json", {"type": type(exc).__name__, "message": str(exc)})
        if repository is not None:
            repository.finish_run(run_id, "failed", metadata["counts"], None)
        raise
    if repository is not None:
        repository.finish_run(run_id, "completed", metadata["counts"], result.metrics.model_dump(mode="json"))
    _write_json(directory / "trace_verification.json", verification)
    _write_json(directory / "trace_exports.json", verification)
    return {
        "run_id": run_id,
        "status": "completed",
        "split": split,
        "agent_version": agent_version,
        "report_path": str(report_path),
        "artifact_dir": str(directory),
        "answer_outcomes": [_outcome_dict(outcome) for outcome in outcomes],
        "metrics": result.metrics.model_dump(mode="json"),
        "counts": metadata["counts"],
        "trace_ids": trace_ids,
        "trace_verification": verification,
    }


__all__ = ["RecordingTracer", "run_split"]
